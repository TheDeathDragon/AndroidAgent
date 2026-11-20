# Keep the entry point for app_process
-keep class la.shiro.agent.Server {
    public static void main(java.lang.String[]);
}

# Keep BuildConfig for version info
-keep class la.shiro.agent.BuildConfig { *; }

# Prevent R8 from inlining API 34+ hidden API calls into classes loaded on API 30-33
-keep class la.shiro.agent.VirtualDisplayFactory { *; }
