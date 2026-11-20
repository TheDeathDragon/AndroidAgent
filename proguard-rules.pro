-keep class la.shiro.agent.Server {
    public static void main(java.lang.String[]);
}

-keep class la.shiro.agent.BuildConfig { *; }

# Prevent R8 from inlining API 34+ hidden API calls into classes loaded on API 30-33
-keep class la.shiro.agent.Api34InputInjector { *; }
-keep class la.shiro.agent.VirtualDisplayFactory { *; }
