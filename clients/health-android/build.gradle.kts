plugins {
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.serialization") version "2.0.21"
}

repositories {
    mavenCentral()
}

dependencies {
    implementation(kotlin("stdlib"))
    // JSON serialization for gateway API contracts (replaces manual JSON parsing).
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    // OkHttp for TLS-pinned HTTP client (certificate pinning via gatewayCAFile).
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
