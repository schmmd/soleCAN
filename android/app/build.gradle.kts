plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Single source of truth for the dashboard HTML lives at the repo root
// (../../dashboard.html). The ESP32 firmware build copies it the same way.
val copyDashboardAsset by tasks.registering(Copy::class) {
    from(rootProject.file("../dashboard.html"))
    into(layout.projectDirectory.dir("src/main/assets"))
}
tasks.named("preBuild") { dependsOn(copyDashboardAsset) }

// Source git SHA. Prefer the GIT_SHA env var (set by the Docker build, whose
// context has no .git); otherwise shell out. Falls back to "unknown".
fun gitSha(): String {
    System.getenv("GIT_SHA")?.takeIf { it.isNotBlank() }?.let { return it }
    return try {
        val proc = ProcessBuilder("git", "rev-parse", "--short", "HEAD")
            .directory(rootProject.projectDir.parentFile)
            .redirectErrorStream(true)
            .start()
        val out = proc.inputStream.bufferedReader().readText().trim()
        if (proc.waitFor() == 0 && out.isNotEmpty()) {
            val dirty = ProcessBuilder("git", "diff", "--quiet")
                .directory(rootProject.projectDir.parentFile)
                .start().waitFor() != 0
            out + if (dirty) "-dirty" else ""
        } else "unknown"
    } catch (_: Exception) { "unknown" }
}
val gitShaValue = gitSha()

// Release tag — non-empty only when this commit is a tagged release. Prefer the
// GIT_VERSION env var (CI passes the tag on tag builds; the Docker context has no
// .git); otherwise `git describe --tags --exact-match`. Empty when not on a tag.
fun gitVersion(): String {
    System.getenv("GIT_VERSION")?.let { return it.trim() }
    return try {
        val proc = ProcessBuilder("git", "describe", "--tags", "--exact-match")
            .directory(rootProject.projectDir.parentFile)
            .redirectErrorStream(true)
            .start()
        val out = proc.inputStream.bufferedReader().readText().trim()
        if (proc.waitFor() == 0) out else ""
    } catch (_: Exception) { "" }
}
val gitVersionValue = gitVersion()

android {
    namespace = "com.schmitztech.solectrac.dashboard"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.schmitztech.solectrac.dashboard"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = if (gitVersionValue.isNotEmpty()) gitVersionValue else "1.0+$gitShaValue"
        buildConfigField("String", "GIT_SHA", "\"$gitShaValue\"")
        buildConfigField("String", "GIT_VERSION", "\"$gitVersionValue\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
}
