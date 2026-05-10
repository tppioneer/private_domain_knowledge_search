# 积分服务数据契约

## 强制规范

- 积分变动方法必须记录change_log，并返回流水号
- 正例: changeId := logPointsChange(bizId, uid, points)
- 反例: 直接调用SDK后未记录流水
