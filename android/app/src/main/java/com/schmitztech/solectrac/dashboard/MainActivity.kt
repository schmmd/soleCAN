package com.schmitztech.solectrac.dashboard

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.schmitztech.solectrac.dashboard.databinding.ActivityMainBinding
import org.json.JSONObject

/**
 * Hosts the shared dashboard.html (the repo-root copy, staged into assets/ by
 * the copyDashboardAsset gradle task) inside a WebView and pipes BLE-pushed
 * JSON into it via window.dispatchDashboardUpdate.
 */
class MainActivity : AppCompatActivity(), BleClient.Listener {

    private lateinit var b: ActivityMainBinding
    private lateinit var ble: BleClient

    // The live WebView. Starts as the layout's, but is replaced wholesale if the
    // renderer process dies — so never reference b.web after onCreate.
    private lateinit var web: WebView

    // True once dashboard.html has called DashboardBridge.ready() — only then is
    // it safe to invoke dispatchDashboardUpdate.
    private var bridgeReady = false
    private var pending: String? = null

    // Set when a request comes back denied with rationale suppressed: re-requesting
    // would silently auto-deny, so the Scan button routes to app settings instead.
    private var permissionsPermanentlyDenied = false

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        if (results.values.all { it }) {
            permissionsPermanentlyDenied = false
            ble.start()
        } else {
            permissionsPermanentlyDenied = results.filterValues { !it }.keys
                .any { !shouldShowRequestPermissionRationale(it) }
            b.status.text = if (permissionsPermanentlyDenied)
                "Bluetooth permission denied — tap Scan to open Settings"
            else
                "Bluetooth permission denied"
        }
    }

    private val enableBtLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) ble.rescan()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        web = b.web
        configureWebView(web)
        ble = BleClient(this, this)

        b.scanBtn.setOnClickListener { onScanClicked() }
    }

    override fun onStart() {
        super.onStart()
        val missing = missingPermissions()
        if (missing.isEmpty()) ble.start()
        else permissionLauncher.launch(missing.toTypedArray())
    }

    override fun onStop() {
        super.onStop()
        ble.stop()
    }

    private fun requiredPermissions(): List<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            listOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

    private fun missingPermissions(): List<String> = requiredPermissions().filter {
        ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
    }

    private fun bluetoothEnabled(): Boolean =
        getSystemService(BluetoothManager::class.java)?.adapter?.isEnabled == true

    private fun onScanClicked() {
        val missing = missingPermissions()
        when {
            missing.isNotEmpty() && permissionsPermanentlyDenied ->
                startActivity(
                    Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", packageName, null)
                    )
                )
            missing.isNotEmpty() ->
                permissionLauncher.launch(missing.toTypedArray())
            !bluetoothEnabled() ->
                enableBtLauncher.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
            else -> ble.rescan()
        }
    }

    private fun configureWebView(web: WebView) {
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.addJavascriptInterface(Bridge(), "DashboardBridge")
        web.webViewClient = object : WebViewClient() {
            // Returning true claims the renderer's death instead of letting the
            // framework kill the whole app; the WebView instance is unusable
            // afterwards and must be destroyed and replaced.
            override fun onRenderProcessGone(
                view: WebView, detail: RenderProcessGoneDetail
            ): Boolean {
                Log.w(TAG, "WebView renderer gone (crashed=${detail.didCrash()}) — recreating")
                recreateWebView(view)
                return true
            }
        }
        web.loadUrl("file:///android_asset/dashboard.html")
    }

    private fun recreateWebView(dead: WebView) {
        bridgeReady = false
        val parent = dead.parent as? ViewGroup ?: return
        val index = parent.indexOfChild(dead)
        val lp = dead.layoutParams
        parent.removeView(dead)
        dead.destroy()
        web = WebView(this)
        parent.addView(web, index, lp)
        configureWebView(web)
    }

    inner class Bridge {
        /** Called by dashboard.html once it has registered dispatchDashboardUpdate. */
        @JavascriptInterface
        fun ready() {
            runOnUiThread {
                bridgeReady = true
                pending?.let { pushJsonToWebView(it); pending = null }
            }
        }

        @JavascriptInterface
        fun appVersion(): String = BuildConfig.GIT_SHA
    }

    private fun pushJsonToWebView(json: String) {
        // JSONObject.quote handles all string escapes safely for JS embedding.
        val escaped = JSONObject.quote(json)
        web.evaluateJavascript("window.dispatchDashboardUpdate($escaped);", null)
    }

    // ── BleClient.Listener ────────────────────────────────────────────────────

    override fun onStateChange(state: BleClient.State, detail: String) {
        b.status.text = detail
        val connected = state == BleClient.State.CONNECTED
        // Hide the half-rendered dashboard whenever we're not connected — show a
        // plain "Disconnected" overlay instead.
        b.disconnectedOverlay.visibility = if (connected) View.GONE else View.VISIBLE
        b.disconnectedText.text = when (state) {
            BleClient.State.SCANNING   -> "Scanning…"
            BleClient.State.CONNECTING -> "Connecting…"
            BleClient.State.BT_OFF     -> "Bluetooth off"
            else                       -> "Disconnected"
        }
        b.statusBar.visibility = if (connected) View.GONE else View.VISIBLE
        b.scanBtn.visibility = when (state) {
            BleClient.State.IDLE, BleClient.State.DISCONNECTED,
            BleClient.State.ERROR, BleClient.State.BT_OFF -> View.VISIBLE
            else -> View.GONE
        }
    }

    override fun onJson(json: String) {
        if (bridgeReady) pushJsonToWebView(json) else pending = json
    }

    companion object {
        private const val TAG = "MainActivity"
    }
}
