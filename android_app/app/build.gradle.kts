import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Tier 11 — release signing. keystore.properties + jarvis-upload-key.jks (both gitignored,
// see .gitignore and keystore.properties' own header comment) hold the actual upload key;
// loaded conditionally so a checkout without them (a fresh clone, before Devin's copied the
// keystore over) still builds a debug APK fine — only `bundleRelease`/`assembleRelease`
// need this, and Gradle only evaluates signingConfigs.release lazily when something
// actually requests a release build.
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties()
val hasReleaseSigning = keystorePropertiesFile.exists()
if (hasReleaseSigning) {
    keystoreProperties.load(keystorePropertiesFile.inputStream())
}

// Tier 10 — see the root build.gradle.kts comment on why this is conditional. Once Devin
// creates the Firebase project and drops the real google-services.json in this directory
// (android_app/app/google-services.json — see android_app/README.md), this starts applying
// automatically on the next build; nothing else in this file needs to change.
val hasFirebaseConfig = file("google-services.json").exists()
if (hasFirebaseConfig) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "com.dhaaselab.jarvis"
    // compileSdk/targetSdk 34 — matches the platform actually installed for this build.
    // Confirm the current Play Console requirement at submission time (Tier 11); bumping
    // this later is a one-line change plus reinstalling that SDK platform, not a rewrite.
    compileSdk = 34

    defaultConfig {
        applicationId = "com.dhaaselab.jarvis"
        // minSdk 26 (Android 8.0, 2017) — comfortably covers the overwhelming majority of
        // active devices while staying clear of pre-Oreo WebView/notification-channel
        // quirks that would otherwise need their own compatibility shims for zero real
        // benefit on a personal client app.
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                // rootProject.file(), not file() — keystore.properties' storeFile path
                // (jarvis-upload-key.jks) is relative to android_app/, where both files
                // actually live, not android_app/app/ where a plain file() call here would
                // otherwise resolve it (real error hit on first build: "Keystore file
                // .../app/jarvis-upload-key.jks not found").
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
            // Without hasReleaseSigning, this falls back to the debug signing config
            // (AGP's default when none is set) — fine for confirming a release build
            // actually compiles, but NOT upload-ready; bundleRelease's own output is
            // checked for real signing before Tier 11 calls it done (see task.md).
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
        buildConfig = true  // Part 3 — exposes BuildConfig.VERSION_CODE, compared against server.py's android_min_version_code (MainActivity's checkNativeShellVersion()). AGP 8.x defaults this off, needs the explicit opt-in.
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.browser:browser:1.8.0")  // Tier 9 — Custom Tabs for OAuth/external links

    // Tier 10 — declared unconditionally (safe even without google-services.json; only
    // actually initializes at runtime if a real config is present — see
    // JarvisFirebaseMessagingService's own guards). The BoM pins compatible versions across
    // every firebase-* artifact so only this one version needs to change over time.
    implementation(platform("com.google.firebase:firebase-bom:33.5.1"))
    implementation("com.google.firebase:firebase-messaging-ktx")
}
