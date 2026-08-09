// Root build file. AGP 8.6 + Kotlin 2.0 + Gradle 8.9 — a well-established, thoroughly
// documented combination rather than chasing the bleeding-edge AGP 9.x line (which requires
// Gradle 8.11+ and changed how Kotlin support is wired in) for a first build in an
// environment with no prior Android tooling to debug against. Per the spec's own guidance:
// confirm the actual current required AGP/target-SDK level in the Play Console at
// submission time (Tier 11) rather than treating this pin as permanent.
plugins {
    id("com.android.application") version "8.6.1" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    // Tier 10 — declared here (available) but NOT applied here; app/build.gradle.kts only
    // applies it if google-services.json actually exists. The plugin hard-fails the build
    // if that file is missing, and Firebase project creation is a real account-level step
    // (Google account, out of scope for anything this agent can do on its own — see
    // android_app/README.md) — the whole rest of this project needs to keep building and
    // running normally whether or not that step has happened yet.
    id("com.google.gms.google-services") version "4.4.2" apply false
}
