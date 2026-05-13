# Keep the entry point for app_process
-keep class la.shiro.agent.Server {
    public static void main(java.lang.String[]);
}

# Keep BuildConfig for version info
-keep class la.shiro.agent.BuildConfig { *; }
