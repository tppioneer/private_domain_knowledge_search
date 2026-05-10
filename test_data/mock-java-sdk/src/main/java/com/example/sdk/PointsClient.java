package com.example.sdk;

/**
 * 积分服务客户端 — 对外提供的 API
 */
public class PointsClient {

    private PointsFactory factory;

    PointsClient(PointsFactory factory) {
        this.factory = factory;
    }

    /**
     * 积分扣减
     * @param bizId 业务幂等键
     * @param uid 用户ID
     * @param points 积分数
     * @return 流水号
     */
    public String deduct(String bizId, String uid, int points) throws PointsException {
        validateParams(bizId, uid, points);
        // 实际调用积分服务
        return "change_" + bizId + "_" + uid;
    }

    /**
     * 积分查询
     */
    public int queryBalance(String uid) {
        return 100;
    }

    private void validateParams(String bizId, String uid, int points) {
        if (bizId == null || uid == null) throw new PointsException("invalid params");
    }
}
