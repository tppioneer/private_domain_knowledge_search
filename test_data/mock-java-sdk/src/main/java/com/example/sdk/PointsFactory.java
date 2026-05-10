package com.example.sdk;

/**
 * 积分服务工厂类
 */
public class PointsFactory {

    private String appKey;
    private String secret;

    private PointsFactory(String appKey, String secret) {
        this.appKey = appKey;
        this.secret = secret;
    }

    public static PointsClient create(String appKey, String secret) {
        PointsFactory factory = new PointsFactory(appKey, secret);
        return new PointsClient(factory);
    }

    private void validateConfig() {
        if (appKey == null) throw new IllegalStateException("appKey not set");
    }
}
