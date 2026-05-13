import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

plugins {
    id("com.android.application") version "8.10.0-rc01"
    id("org.jetbrains.kotlin.android") version "2.0.21"
}

fun executeCommand(vararg args: String): String {
    return try {
        val process = ProcessBuilder(*args)
            .directory(rootDir)
            .redirectErrorStream(true)
            .start()
        process.inputStream.bufferedReader().readText().trim()
    } catch (_: Exception) {
        ""
    }
}

val yearMonth: String = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyMM"))
val commitCountRaw: String = executeCommand("git", "rev-list", "--count", "HEAD")
val commitCount: Int = commitCountRaw.toIntOrNull() ?: 0
val commitCountPadded: String = commitCount.toString().padStart(4, '0')
val gitHash: String = executeCommand("git", "rev-parse", "--short", "HEAD").ifEmpty { "unknown" }
val agentVersion: String = "$yearMonth.$commitCountPadded"
val agentVersionCode: Int = yearMonth.toInt() * 10000 + commitCount

android {
    namespace = "la.shiro.agent"
    compileSdk = 36

    defaultConfig {
        applicationId = "la.shiro.agent"
        minSdk = 30
        targetSdk = 36
        versionCode = agentVersionCode
        versionName = agentVersion
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = "11"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    lint {
        disable += "HiddenApiUsage"
        disable += "PrivateApi"
    }
}

dependencies {
    // Zero external dependencies - only Android framework APIs
}
