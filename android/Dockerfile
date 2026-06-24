# Reproducible build environment for the Solectrac Android dashboard app.
#
# The build context is the repo ROOT, because the Android build copies the
# canonical dashboard.html from there (see app/build.gradle.kts ->
# copyDashboardAsset). From the repo root:
#
#   docker build -f android/Dockerfile -t solectrac-android .
#
# Extract the APK onto the host so you can install it (e.g. adb install,
# Android Studio APK install, or transfer to the phone for sideloading):
#
#   docker run --rm -v "$PWD/out:/out" solectrac-android
#   # -> out/app-debug.apk
#
# Pinned: JDK 17 + Gradle 8.7 + Android SDK platform 34 / build-tools 34.0.0
# (matches compileSdk / targetSdk in app/build.gradle.kts and the wrapper
# version called out in CLAUDE.md).
FROM eclipse-temurin:17-jdk-noble

# sdkmanager prerequisites.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools

# Android cmdline-tools (commandlinetools-linux-11076708 = version 12.0).
RUN mkdir -p "$ANDROID_HOME/cmdline-tools" \
    && curl -fsSL https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -o /tmp/clt.zip \
    && unzip -q /tmp/clt.zip -d "$ANDROID_HOME/cmdline-tools" \
    && mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" \
    && rm /tmp/clt.zip

# Accept SDK licenses and install platform + build-tools matching
# compileSdk / targetSdk = 34 in app/build.gradle.kts.
RUN yes | sdkmanager --licenses > /dev/null \
    && sdkmanager --install "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# Pin Gradle to the same version the wrapper would use natively.
ARG GRADLE_VERSION=8.7
RUN curl -fsSL https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip -o /tmp/gradle.zip \
    && unzip -q /tmp/gradle.zip -d /opt \
    && rm /tmp/gradle.zip
ENV PATH=$PATH:/opt/gradle-${GRADLE_VERSION}/bin

WORKDIR /project

# Bring in the Android tree and the canonical dashboard.html. The
# `copyDashboardAsset` Gradle task reads ../dashboard.html relative to
# the Android root project, so the relative path matches the native build.
COPY android/ ./android/
COPY dashboard.html ./dashboard.html

# Host-resolved git SHA (the build context omits .git). Pass with
# --build-arg GIT_SHA=$(git rev-parse --short HEAD); falls back to "unknown".
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# Build the debug APK. assembleRelease would need a signing config that
# isn't checked in, so debug is the reproducible default.
WORKDIR /project/android
RUN gradle --no-daemon :app:assembleDebug

# Default action: copy the APK out to a mounted /out volume on the host.
CMD ["sh", "-c", "set -e; mkdir -p /out; cp -v /project/android/app/build/outputs/apk/debug/app-debug.apk /out/; echo 'APK copied to /out/app-debug.apk — install with `adb install`.'"]
