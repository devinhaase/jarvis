pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "Jarvis"
include(":app")

// Real bug hit live: this whole project lives under OneDrive\Desktop\..., and OneDrive's
// sync engine holds transient file locks on the build/ directory's thousands of rapidly-
// changing intermediate files, which Gradle then fails to delete/overwrite between builds
// ("Unable to delete directory ... Failed to delete some children") — not a Gradle or
// AGP bug, a real conflict between cloud file sync and a build tool's own churn. Fixed by
// redirecting every module's build output outside the synced tree entirely, rather than
// fighting OneDrive's sync timing or retrying — nothing here needs to be backed up or
// synced anyway, it's 100% regenerable from source.
gradle.beforeProject {
    layout.buildDirectory.set(File("C:/AndroidBuildCache/jarvis/${project.path.replace(":", "_").ifEmpty { "root" }}"))
}
