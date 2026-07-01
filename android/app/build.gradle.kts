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

android {
    namespace = "com.schmitztech.solectrac.dashboard"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.schmitztech.solectrac.dashboard"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0+${gitSha()}"
        buildConfigField("String", "GIT_SHA", "\"${gitSha()}\"")
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
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
}
