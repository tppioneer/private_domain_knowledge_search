# 积分服务 API 文档

## 背景介绍

积分服务是订单系统的核心组件，负责用户积分的扣减、查询和退还。所有接口需要进行 OAuth2.0 认证，积分变动操作需要记录审计日志。

## 常见问题

### Q: 积分扣减失败如何处理？
A: 检查 bizId 是否重复提交。系统使用 bizId+uid 作为幂等键。

## API

### POST /api/v1/points/deduct

积分扣减接口，需要传入业务幂等键。

- **认证方式**: Bearer Token
- **Content-Type**: application/json

**Request Body:**
```json
{
    "bizId": "ORD-2026-001",
    "uid": "user_123",
    "points": 10
}
```

**Response 200:**
```json
{
    "code": 0,
    "data": {
        "changeId": "CHG-2026-001",
        "balance": 90
    }
}
```

**调用示例:**
```bash
curl -X POST https://api.example.com/api/v1/points/deduct \
  -H "Authorization: Bearer xxx" \
  -H "Content-Type: application/json" \
  -d '{"bizId":"ORD-2026-001","uid":"user_123","points":10}'
```

### GET /api/v1/points/balance/{uid}

查询用户积分余额。

**路径参数:**
| 参数 | 类型 | 描述 |
|------|------|------|
| uid | string | 用户ID |

**Response 200:**
```json
{
    "code": 0,
    "data": {
        "uid": "user_123",
        "balance": 90
    }
}
```
