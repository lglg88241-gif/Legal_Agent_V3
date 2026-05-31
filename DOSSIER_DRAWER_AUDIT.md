# 案卷抽屉模块审计与精修方案

## 当前模块清单

### 1. 已连接主体 (confirmedEntities)
- **数据源**: `selectedDossierConfirmedCanonicalEntities` - status='canonical' 的实体
- **功能**: 展示已归并的规范实体，显示来源会话数、详细对话链接
- **状态**: ✓ 核心功能，保留
- **缺失**: ✗ 缺少"手动合并到其他实体"按钮

### 2. 跨会话同一实体确认 (crossConversationTasks)
- **数据源**: `selectedDossierCanonicalCrossConversationTasks` - task_type='cross_conversation_same_entity'
- **功能**: 展示中置信度的跨会话实体候选，需要用户确认是否合并
- **状态**: ✓ 核心功能，保留
- **问题**: 用户反馈没看到任务（需要验证数据流）

### 3. 待确认连接 (pendingIdentityEntities)
- **数据源**: `selectedDossierPendingCanonicalIdentityEntities` - status='pending_identity'
- **功能**: 旧版 participant 系统的遗留模块
- **状态**: ✗ 降级模块，应该移除或隐藏
- **原因**: 新架构使用 canonical entities + confirmation_tasks，不再需要 pending_identity status

### 4. 待命名主体 (pendingNamingEntities)
- **数据源**: `selectedDossierPendingCanonicalNamingEntities` - status='pending_naming'
- **功能**: 旧版 participant 系统的遗留模块
- **状态**: ✗ 降级模块，应该移除或隐藏
- **原因**: 新架构使用 party_naming task，不再需要 pending_naming status

### 5. 待确认事项 (selfAnchorIdentityTasks)
- **数据源**: `selectedDossierSelfAnchorIdentityTasks` - task_type='entity_identity', subject='self_anchor'
- **功能**: 确认"我"是否与已命名当事人是同一人
- **状态**: ✓ 核心功能，保留
- **问题**: 应该主动弹窗，而不是只在抽屉里展示

### 6. 未连接主体锚点 (isolatedAnchorEntities)
- **数据源**: `selectedDossierCanonicalIsolatedAnchorEntities` - status='isolated_anchor'/'generic_label'/'unconfirmed_label'
- **功能**: 展示只在单个会话中出现的主体
- **状态**: ⚠️ 边缘功能，可以保留但降低优先级
- **原因**: 单会话主体在跨会话归并场景下价值较低

### 7. 待命名确认 (partyNamingTasks)
- **数据源**: `selectedDossierCanonicalPartyNamingTasks` - task_type='party_naming'
- **功能**: 第一人称陈述时，让用户确认当事人名称
- **状态**: ✓ 核心功能，保留
- **问题**: 应该主动弹窗，而不是只在抽屉里展示

## 精修方案

### 阶段1: 清理降级模块
1. **移除"待确认连接"模块** (pendingIdentityEntities)
2. **移除"待命名主体"模块** (pendingNamingEntities)
3. **降低"未连接主体锚点"优先级**，折叠到"高级"区域

### 阶段2: 增强核心功能
1. **已连接主体**: 增加"合并到其他实体"按钮
   - 点击后弹出选择器，列出所有其他 canonical entities
   - 调用 `manualMergeDossierCanonicalEntities` API
   - 成功后刷新案卷图谱

2. **跨会话同一实体确认**: 保持现有功能
   - 验证数据流是否正常
   - 确保 medium confidence 任务正确显示

3. **待确认事项 (selfAnchorIdentityTasks)**: 主动弹窗
   - 检测到 self_anchor identity task 时，立即弹窗
   - 不再只在抽屉里展示

4. **待命名确认 (partyNamingTasks)**: 主动弹窗
   - 检测到 party_naming task 时，立即弹窗
   - 不再只在抽屉里展示

### 阶段3: 优化布局
1. **主视图**: 只展示核心模块
   - 已连接主体
   - 跨会话同一实体确认
   - （主动弹窗的任务不在抽屉里展示）

2. **折叠区域**: 高级/调试信息
   - 未连接主体锚点
   - 统计信息
   - Trace 信息

## 实施优先级

### P0 (立即修复)
1. 增加"手动合并"按钮到"已连接主体"模块
2. 移除"待确认连接"和"待命名主体"模块

### P1 (本轮完成)
3. 实现 party_naming task 主动弹窗
4. 实现 self_anchor identity task 主动弹窗

### P2 (后续优化)
5. 优化布局，降低"未连接主体锚点"优先级
6. 验证跨会话同一实体确认的数据流

## 技术实现要点

### 手动合并功能
```javascript
// 在 App.vue 中增加 handler
async function handleManualMergeCanonicalEntities(sourceEntity, targetEntity) {
  // 确认弹窗
  await ElMessageBox.confirm(
    `确认将 "${sourceEntity.label}" 合并到 "${targetEntity.label}"？`,
    '手动合并实体'
  )
  
  // 调用 API
  await manualMergeDossierCanonicalEntities(dossierId, {
    source_entity_id: sourceEntity.id,
    target_entity_id: targetEntity.id,
    decision: 'same'
  })
  
  // 刷新
  await loadSelectedDossierCanonicalGraph(dossierId)
}
```

### 主动弹窗逻辑
```javascript
// 在 loadSelectedDossierIntelligence 后检查
watch(selectedDossierCanonicalPartyNamingTasks, (tasks) => {
  if (tasks.length > 0 && !partyNamingModalVisible.value) {
    activePartyNamingTask.value = tasks[0]
    partyNamingModalVisible.value = true
  }
})
```

## 预期效果

### 用户体验改进
1. ✓ 抽屉更简洁，只展示核心功能
2. ✓ 关键任务主动弹窗，不会被忽略
3. ✓ 手动合并入口清晰可见
4. ✓ 降级模块不再干扰主流程

### 架构一致性
1. ✓ 完全基于 canonical entities + confirmation_tasks
2. ✓ 移除旧版 participant 系统的遗留模块
3. ✓ 统一的任务驱动交互模式
