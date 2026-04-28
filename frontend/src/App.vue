<template>
  <div class="system-banner" :class="systemStatus.ready ? 'ready' : 'loading'">
    <div class="system-banner-content">
      <div class="system-banner-main">
        <el-icon v-if="systemStatus.ready" class="banner-icon"><CircleCheckFilled /></el-icon>
        <el-icon v-else class="banner-icon rotating"><Loading /></el-icon>
        <span>{{ systemStatus.ready ? '系统已就绪' : '系统核心组件初始化中...' }}</span>
      </div>
      <div class="system-banner-meta">
        <span>Chroma {{ componentLabel(systemStatus.components.chroma) }}</span>
        <span>Neo4j {{ componentLabel(systemStatus.components.neo4j) }}</span>
        <span>云端 LLM {{ componentLabel(systemStatus.components.llm) }}</span>
        <span>Celery {{ componentLabel(systemStatus.components.celery) }}</span>
      </div>
    </div>
  </div>

  <el-container class="app-container">
    <el-header class="app-header">
      <div class="header-left">
        <el-icon :size="22"><Promotion /></el-icon>
        <span class="app-title">法律智能工作台</span>
      </div>
      <div class="header-right">
        <span class="user-chip">{{ currentUsername }}</span>
        <el-button text @click="openDossierDrawer">
          案卷
        </el-button>
        <el-button
          text
          @click="handleSummary"
          :loading="summaryLoading"
          :disabled="!systemStatus.ready || !currentConversationId"
        >
          <el-icon><Document /></el-icon>总结摘要
        </el-button>
        <el-button text @click="handleExport" :disabled="!currentConversationId">
          <el-icon><Download /></el-icon>导出
        </el-button>
        <el-button
          class="clear-reset-button"
          text
          type="danger"
          @click="handleClearReset"
          :disabled="!currentConversationId"
        >
          <el-icon><Delete /></el-icon>清空聊天并重置案情
        </el-button>
        <el-button text type="danger" @click="handleLogout">退出登录</el-button>
      </div>
    </el-header>

    <el-container class="main-area">
      <el-aside width="260px" class="side-panel left-panel">
        <SessionList
          :sessions="conversations"
          :current-id="currentConversationId"
          :active-summary="currentConversation?.case_summary || ''"
          :loading="conversationListLoading"
          :search-keyword="conversationSearchKeyword"
          :batch-mode="sessionBatchMode"
          :empty-text="conversationListEmptyText"
          @create="createNewConversation"
          @switch="switchConversation"
          @rename="promptRenameConversation"
          @delete="removeConversationV2"
          @search="handleConversationSearch"
          @batch-mode-change="handleSessionBatchModeChange"
          @batch-delete="handleBatchDeleteV2"
        />
      </el-aside>

      <el-main class="chat-area">
        <div class="local-model-card">
          <div class="local-model-header">
            <div class="local-model-title">本地模型加速</div>
            <el-switch
              v-model="localModel.enabled"
              :loading="localModel.loading"
              :disabled="localModel.loading"
              @change="handleLocalModelToggle"
            />
          </div>
          <div class="local-model-status" :class="localModelStatusClass">
            {{ localModelStatusText }}
          </div>
        </div>

        <div v-if="currentConversation" class="conversation-mode-card">
          <div class="conversation-mode-main">
            <div class="conversation-mode-copy">
              <span class="conversation-mode-title">当前模式</span>
              <span class="conversation-mode-subtitle">
                {{ currentConversation.title || `会话 #${currentConversation.id}` }}
              </span>
            </div>
            <div class="conversation-mode-tags">
              <el-tag size="small" effect="light" :type="currentConversationModeTagType">
                {{ currentConversationModeLabel }}
              </el-tag>
              <el-tag
                v-if="currentConversation.case_type"
                size="small"
                effect="plain"
              >
                {{ currentConversation.case_type }}
              </el-tag>
              <el-tag
                v-if="currentConversation.dossier_id"
                size="small"
                effect="plain"
                type="primary"
              >
                {{ currentConversationDossierLabel }}
              </el-tag>
            </div>
          </div>
          <div class="conversation-dossier-row">
            <div class="conversation-dossier-copy">
              <span class="conversation-dossier-title">当前案卷</span>
              <span class="conversation-dossier-subtitle">
                {{
                  currentConversation.dossier_id
                    ? `已绑定 ${currentConversationDossierLabel}`
                    : '当前会话尚未绑定案卷'
                }}
              </span>
            </div>
            <el-button size="small" plain @click="openDossierDrawer">
              {{ currentConversation.dossier_id ? '更换案卷' : '绑定案卷' }}
            </el-button>
          </div>
        </div>

        <div v-if="!systemStatus.ready" class="init-overlay">
          <div class="init-card">
            <el-icon class="init-icon rotating"><Loading /></el-icon>
            <div class="init-title">系统核心组件初始化中...</div>
            <div class="init-subtitle">
              正在预热 Chroma、Neo4j、本地模型与 Celery，输入框将在全部组件可用后自动解锁。
            </div>
          </div>
        </div>

        <el-scrollbar ref="scrollRef" class="chat-scroll">
          <div class="message-list" ref="messageListRef">
            <div v-if="!messages.length" class="empty-state">
              <div class="empty-title">发起新的法律咨询</div>
              <div class="empty-subtitle">
                从左侧选择历史会话，或创建一个新会话后开始提问。
              </div>
            </div>

            <ChatMessage v-for="message in messages" :key="message.id || message.key" :msg="message" />

            <div v-if="sending" class="chat-message bot-msg loading-msg">
              <div class="avatar">
                <el-avatar :size="36" style="background: #67c23a">
                  <el-icon><Service /></el-icon>
                </el-avatar>
              </div>
              <div class="bubble-wrap">
                <div class="bubble loading-bubble">
                  <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                </div>
              </div>
            </div>
          </div>
        </el-scrollbar>

        <div class="input-area" :class="{ locked: !systemStatus.ready }">
          <div class="input-stack">
            <div class="mode-switch-row">
              <span class="mode-switch-label">精准案情梳理</span>
              <el-switch
                v-model="proactiveGraphMode"
                :disabled="sending || !currentConversationId"
                inline-prompt
                active-text="开"
                inactive-text="关"
              />
            </div>
            <el-input
              v-model="inputText"
              type="textarea"
              :rows="2"
              :disabled="!systemStatus.ready || sending || !currentConversationId"
              :placeholder="inputPlaceholder"
              resize="none"
              @keydown="handleKeydown"
            />
          </div>
          <el-button
            type="primary"
            @click="() => sendImpl()"
            :loading="sending"
            :disabled="!systemStatus.ready || !inputText.trim() || !currentConversationId"
          >
            发送
          </el-button>
        </div>
      </el-main>

      <el-aside
        :width="graphSidebarCollapsed ? '72px' : '360px'"
        class="side-panel right-panel"
        :class="{ collapsed: graphSidebarCollapsed }"
      >
        <AnalysisPanel
          :status="systemStatus"
          :analysis="currentAnalysis"
          :graph="currentGraph"
          :collapsed="graphSidebarCollapsed"
          @feedback="handleFeedback"
          @toggle-sidebar="graphSidebarCollapsed = !graphSidebarCollapsed"
        />
      </el-aside>
    </el-container>
  </el-container>

  <el-drawer
    v-model="dossierDrawerVisible"
    title="案卷"
    size="420px"
    destroy-on-close
  >
    <div class="dossier-drawer">
      <div class="dossier-toolbar">
        <div class="dossier-toolbar-copy">
          <span class="dossier-toolbar-title">案卷管理</span>
          <span class="dossier-toolbar-subtitle">创建案卷并绑定当前会话</span>
        </div>
        <el-button size="small" plain @click="refreshDossiers()" :loading="dossierListLoading">
          刷新
        </el-button>
      </div>

      <div class="dossier-current-card">
        <div class="dossier-current-title">当前会话</div>
        <div class="dossier-current-name">
          {{ currentConversation?.title || '未选择会话' }}
        </div>
        <div class="dossier-current-hint">
          {{
            currentConversation?.dossier_id
              ? `已绑定 ${currentConversationDossierLabel}`
              : '可先创建案卷，再将当前会话绑定到案卷'
          }}
        </div>
      </div>

      <div class="dossier-create-card">
        <div class="dossier-section-title">新建案卷</div>
        <el-input
          v-model="dossierForm.title"
          maxlength="255"
          placeholder="案卷标题"
        />
        <el-input
          v-model="dossierForm.case_type"
          maxlength="100"
          placeholder="案由 / 类型，例如劳动争议"
        />
        <el-input
          v-model="dossierForm.summary"
          type="textarea"
          :rows="3"
          maxlength="5000"
          show-word-limit
          resize="none"
          placeholder="案卷摘要（可选）"
        />
        <el-button type="primary" :loading="dossierSaving" @click="handleCreateDossier">
          创建案卷
        </el-button>
      </div>

      <div class="dossier-list-card">
        <div class="dossier-section-title">我的案卷</div>
        <div class="dossier-list-filters">
          <el-input
            v-model="dossierSearchKeyword"
            clearable
            placeholder="搜索标题、案由或摘要"
          />
          <el-radio-group v-model="dossierStatusFilter" size="small" class="dossier-status-filter">
            <el-radio-button label="all">全部</el-radio-button>
            <el-radio-button label="active">进行中</el-radio-button>
            <el-radio-button label="archived">已归档</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="dossierListLoading" class="dossier-list-status">正在加载案卷...</div>
        <el-empty
          v-else-if="!filteredDossiers.length"
          :description="dossierListEmptyDescription"
          :image-size="64"
        />
        <div v-else class="dossier-list">
          <div
            v-for="dossier in filteredDossiers"
            :key="dossier.id"
            class="dossier-item"
            :class="{ active: dossier.id === selectedDossierId }"
            @click="handleSelectDossier(dossier)"
          >
            <div class="dossier-item-main">
              <div class="dossier-item-copy">
                <div class="dossier-item-title">
                  {{ dossier.title || `案卷 #${dossier.id}` }}
                </div>
                <div class="dossier-item-meta">
                  <el-tag size="small" effect="plain" :type="dossierStatusTagType(dossier.status)">
                    {{ dossierStatusLabel(dossier.status) }}
                  </el-tag>
                  <span v-if="dossier.case_type">{{ dossier.case_type }}</span>
                  <span v-else>未分类</span>
                </div>
                <div class="dossier-item-meta dossier-item-meta-secondary">
                  <span>{{ `最近更新：${formatDossierConversationTime(dossier.updated_at) || '未知'}` }}</span>
                  <span v-if="dossier.conversation_count !== undefined && dossier.conversation_count !== null">
                    {{ `绑定会话 ${dossier.conversation_count || 0} 个` }}
                  </span>
                </div>
                <div class="dossier-item-summary">
                  {{ dossier.summary || '暂无案卷摘要' }}
                </div>
              </div>
              <div class="dossier-item-actions">
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :disabled="!currentConversationId || dossierBindingId === dossier.id || dossier.id === currentConversation?.dossier_id || dossier.status === 'archived'"
                  :loading="dossierBindingId === dossier.id"
                  @click.stop="handleAttachDossier(dossier)"
                >
                  {{ dossier.id === currentConversation?.dossier_id ? '已绑定当前会话' : '绑定当前会话' }}
                </el-button>
                <el-button
                  size="small"
                  text
                  type="warning"
                  :disabled="dossier.status === 'archived'"
                  @click.stop="handleArchiveDossier(dossier)"
                >
                  {{ dossier.status === 'archived' ? '已归档' : '归档' }}
                </el-button>
                <el-button
                  size="small"
                  text
                  type="danger"
                  @click.stop="handleHardDeleteDossier(dossier)"
                >
                  删除
                </el-button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="dossier-detail-card">
        <div class="dossier-detail-header">
          <div class="dossier-toolbar-copy">
            <span class="dossier-section-title">案卷详情</span>
            <span class="dossier-toolbar-subtitle">查看案卷摘要并编辑基本信息</span>
          </div>
          <div v-if="selectedDossierId" class="dossier-detail-actions">
            <el-button
              size="small"
              type="primary"
              plain
              @click="handleCreateConversationFromSelectedDossier"
            >
              基于此案卷新建会话
            </el-button>
            <el-button
              size="small"
              plain
              @click="loadSelectedDossierDetail(selectedDossierId)"
              :loading="dossierDetailLoading"
            >
              刷新详情
            </el-button>
            <el-button
              size="small"
              type="warning"
              plain
              :disabled="selectedDossierDetail?.status === 'archived'"
              @click="handleArchiveDossier(selectedDossierDetail)"
            >
              {{ selectedDossierDetail?.status === 'archived' ? '已归档' : '归档案卷' }}
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              @click="handleHardDeleteDossier(selectedDossierDetail)"
            >
              删除案卷
            </el-button>
          </div>
        </div>

        <div v-if="dossierDetailLoading" class="dossier-list-status">正在加载案卷详情...</div>
        <el-empty
          v-else-if="!selectedDossierDetail"
          description="请先从上方选择一个案卷"
          :image-size="64"
        />
        <div v-else class="dossier-detail-body">
          <div class="dossier-detail-meta">
            <el-tag size="small" effect="plain" :type="dossierStatusTagType(selectedDossierDetail.status)">
              {{ dossierStatusLabel(selectedDossierDetail.status) }}
            </el-tag>
            <span>{{ `绑定会话 ${selectedDossierConversationCount} 个` }}</span>
            <span>{{ `ID #${selectedDossierDetail.id}` }}</span>
          </div>

          <el-input
            v-model="dossierDetailForm.title"
            maxlength="255"
            placeholder="案卷标题"
          />
          <el-input
            v-model="dossierDetailForm.case_type"
            maxlength="100"
            placeholder="案由 / 类型"
          />
          <div class="dossier-detail-conversations-title">手动摘要</div>
          <el-input
            v-model="dossierDetailForm.summary"
            type="textarea"
            :rows="4"
            maxlength="5000"
            show-word-limit
            resize="none"
            placeholder="案卷摘要"
          />
          <el-select v-model="dossierDetailForm.status" placeholder="状态">
            <el-option label="进行中" value="active" />
            <el-option label="已归档" value="archived" />
            <el-option label="已关闭" value="closed" />
          </el-select>
          <el-button
            type="primary"
            :loading="dossierDetailSaving"
            @click="handleUpdateDossierDetail"
          >
            保存案卷信息
          </el-button>

          <div class="dossier-auto-summary-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-detail-conversations-title">智能摘要</div>
              <el-button
                size="small"
                type="primary"
                plain
                :loading="dossierSummaryGenerating"
                @click="handleGenerateDossierSummary"
              >
                {{ selectedDossierDetail.auto_summary ? '刷新智能摘要' : '智能生成摘要' }}
              </el-button>
            </div>
            <div
              v-if="selectedDossierDetail.auto_summary_updated_at || selectedDossierDetail.auto_summary_source_count"
              class="dossier-auto-summary-meta"
            >
              <span v-if="selectedDossierDetail.auto_summary_updated_at">
                {{ `最近生成：${formatDossierConversationTime(selectedDossierDetail.auto_summary_updated_at)}` }}
              </span>
              <span>{{ `来源会话：${selectedDossierDetail.auto_summary_source_count || 0}` }}</span>
            </div>
            <div class="dossier-auto-summary-content">
              {{ selectedDossierDetail.auto_summary || '暂无智能摘要，可基于已绑定会话生成。' }}
            </div>
          </div>

          <div class="dossier-coordination-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-toolbar-copy">
                <div class="dossier-detail-conversations-title">案卷实体连接</div>
                <span class="dossier-toolbar-subtitle">
                  只展示连接多个会话的关键主体、待确认主体和未连接主体锚点。会话仅作为来源和跳转目标。
                </span>
              </div>
              <el-button
                size="small"
                plain
                :loading="dossierCoordinationGraphLoading || dossierIntelligenceLoading"
                @click="handleRefreshDossierCoordination"
              >
                刷新实体连接
              </el-button>
            </div>
            <div v-if="dossierCoordinationGraphLoading" class="dossier-list-status">
              正在加载案卷实体连接...
            </div>
            <div
              v-else-if="dossierCoordinationGraphState.error"
              class="dossier-graph-warning dossier-graph-warning-soft"
            >
              {{ dossierCoordinationGraphState.error }}
            </div>
            <div v-else class="dossier-coordination-body">
              <div class="dossier-coordination-summary">
                <span class="dossier-coordination-chip">
                  已连接 {{ selectedDossierCoordinationStats.connectedEntities }}
                </span>
                <span class="dossier-coordination-chip">
                  待确认 {{ selectedDossierCoordinationStats.pendingIdentity }}
                </span>
                <span class="dossier-coordination-chip">
                  待命名 {{ selectedDossierCoordinationStats.pendingNaming }}
                </span>
                <span class="dossier-coordination-chip">
                  未连接 {{ selectedDossierCoordinationStats.isolatedAnchors }}
                </span>
              </div>

              <div v-if="selectedDossierCoordinationIsEmpty" class="dossier-coordination-empty">
                当前案卷还没有形成可连接的主体线索。绑定更多会话或补充案情后，系统会自动检查。
              </div>

              <template v-else>
                <div
                  v-if="selectedDossierCoordinationConfirmedEntities.length"
                  class="dossier-coordination-group"
                >
                  <div class="dossier-detail-conversations-title">已连接主体</div>
                  <div class="dossier-coordination-entity-list">
                    <div
                      v-for="entity in selectedDossierCoordinationConfirmedEntities"
                      :key="entity.id"
                      class="dossier-coordination-entity-card"
                    >
                      <div class="dossier-coordination-entity-main">
                        <div class="dossier-coordination-entity-title">
                          <strong>{{ entity.label || '未命名主体' }}</strong>
                          <span>{{ `${coordinationEntityConversationCount(entity)} 个来源会话` }}</span>
                        </div>
                        <div class="dossier-coordination-entity-meta">
                          <span>{{ `最佳会话：${coordinationEntityBestConversationLabel(entity)}` }}</span>
                          <span v-if="entity.best_conversation_reason">{{ entity.best_conversation_reason }}</span>
                        </div>
                      </div>
                      <div class="dossier-coordination-entity-actions">
                        <el-button
                          size="small"
                          type="primary"
                          text
                          :disabled="!coordinationEntityHasBestConversation(entity)"
                          @click="handleOpenCoordinationEntityConversation(entity)"
                        >
                          查看最佳会话
                        </el-button>
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  v-if="selectedDossierCoordinationPendingIdentityEntities.length"
                  class="dossier-coordination-group"
                >
                  <div class="dossier-detail-conversations-title">待确认连接</div>
                  <div class="dossier-coordination-entity-list">
                    <div
                      v-for="entity in selectedDossierCoordinationPendingIdentityEntities"
                      :key="entity.id"
                      class="dossier-coordination-entity-card"
                    >
                      <div class="dossier-coordination-entity-main">
                        <div class="dossier-coordination-entity-title">
                          <strong>{{ entity.label || '待确认主体' }}</strong>
                          <span>{{ `${coordinationEntityConversationCount(entity)} 个来源会话` }}</span>
                        </div>
                        <div class="dossier-coordination-entity-meta">
                          <span>{{ `来源称呼：${coordinationEntitySourceLabelText(entity)}` }}</span>
                          <span v-if="coordinationEntityIdentityReason(entity)">
                            {{ coordinationEntityIdentityReason(entity) }}
                          </span>
                        </div>
                      </div>
                      <div class="dossier-coordination-entity-actions">
                        <el-button size="small" type="primary" text @click="handleOpenCoordinationIdentity">
                          去确认
                        </el-button>
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  v-if="selectedDossierCoordinationPendingNamingEntities.length"
                  class="dossier-coordination-group"
                >
                  <div class="dossier-detail-conversations-title">待命名主体</div>
                  <div class="dossier-coordination-entity-list">
                    <div
                      v-for="entity in selectedDossierCoordinationPendingNamingEntities"
                      :key="entity.id"
                      class="dossier-coordination-entity-card"
                    >
                      <div class="dossier-coordination-entity-main">
                        <div class="dossier-coordination-entity-title">
                          <strong>{{ entity.label || '待命名主体' }}</strong>
                          <span>{{ `${coordinationEntityConversationCount(entity)} 个来源会话` }}</span>
                        </div>
                        <div class="dossier-coordination-entity-meta">
                          <span>{{ `来源称呼：${coordinationEntitySourceLabelText(entity)}` }}</span>
                          <span v-if="entity.best_conversation_reason">{{ entity.best_conversation_reason }}</span>
                        </div>
                      </div>
                      <div class="dossier-coordination-entity-actions">
                        <el-button size="small" type="primary" text @click="handleOpenCoordinationNaming">
                          去命名
                        </el-button>
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  v-if="selectedDossierSelfAnchorIdentityTasks.length"
                  class="dossier-coordination-group"
                >
                  <div class="dossier-detail-conversations-title">待确认事项</div>
                  <div class="dossier-intelligence-task-list">
                    <div
                      v-for="(task, index) in selectedDossierSelfAnchorIdentityTasks"
                      :key="`dossier-self-identity-task-${index}`"
                      class="dossier-intelligence-task-card"
                    >
                      <div class="dossier-intelligence-task-header">
                        <strong>{{ task.title || '确认当事人是否为同一人' }}</strong>
                        <span class="dossier-intelligence-task-type">{{ task.task_type }}</span>
                      </div>
                      <div class="dossier-intelligence-task-reason">
                        {{ selfAnchorIdentityTaskPrompt(task) }}
                      </div>
                      <div class="dossier-intelligence-task-meta">
                        <span>{{ task.reason || '暂无原因说明' }}</span>
                        <span v-if="task.confidence">{{ `置信度：${task.confidence}` }}</span>
                      </div>
                      <div class="dossier-intelligence-task-evidence">
                        <span>
                          {{
                            `source_node：${task?.source_node?.graph_node_label || task?.source_node?.raw_label || '当事人'} / ${formatGraphConversationLabel(task?.source_node?.conversation_id)}`
                          }}
                        </span>
                        <span>
                          {{
                            `target_participant：${task?.target_participant?.display_label || '已命名当事人'}`
                          }}
                        </span>
                      </div>
                      <div
                        v-if="selfIdentityTaskIsDifferentMode(task)"
                        class="dossier-intelligence-task-input"
                      >
                        <el-input
                          v-model="selfIdentityTaskState(task).differentDraft"
                          maxlength="50"
                          clearable
                          placeholder="请输入另一个当事人的名称，例如：李四"
                          :disabled="selfIdentityTaskIsSubmitting(task)"
                        />
                      </div>
                      <div
                        v-if="selfIdentityTaskError(task)"
                        class="dossier-graph-warning dossier-graph-warning-soft"
                      >
                        {{ selfIdentityTaskError(task) }}
                      </div>
                      <div class="dossier-intelligence-task-actions">
                        <div class="dossier-inline-actions">
                          <el-button
                            size="small"
                            type="primary"
                            :loading="selfIdentityTaskIsSubmittingAction(task, 'confirm_same')"
                            :disabled="selfIdentityTaskIsSubmitting(task)"
                            @click="handleConfirmSelfIdentitySame(task)"
                          >
                            是，同一个人
                          </el-button>
                          <el-button
                            size="small"
                            :disabled="selfIdentityTaskIsSubmitting(task)"
                            @click="handleOpenSelfIdentityDifferent(task)"
                          >
                            不是，另一个人
                          </el-button>
                          <el-button
                            v-if="selfIdentityTaskIsDifferentMode(task)"
                            size="small"
                            type="primary"
                            plain
                            :loading="selfIdentityTaskIsSubmittingAction(task, 'confirm_different')"
                            :disabled="selfIdentityTaskIsSubmitting(task)"
                            @click="handleConfirmSelfIdentityDifferent(task)"
                          >
                            确认创建
                          </el-button>
                          <el-button
                            size="small"
                            text
                            :disabled="selfIdentityTaskIsSubmitting(task)"
                            @click="handleDismissSelfIdentityTask(task)"
                          >
                            暂不处理
                          </el-button>
                        </div>
                        <span>确认后会刷新案卷智能概览、图谱预览和主体映射。</span>
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  v-if="selectedDossierCoordinationIsolatedAnchorEntities.length"
                  class="dossier-coordination-group"
                >
                  <div class="dossier-detail-conversations-title">未连接主体锚点</div>
                  <div class="dossier-coordination-entity-list">
                    <div
                      v-for="entity in selectedDossierCoordinationIsolatedAnchorEntities"
                      :key="entity.id"
                      class="dossier-coordination-entity-card"
                    >
                      <div class="dossier-coordination-entity-main">
                        <div class="dossier-coordination-entity-title">
                          <strong>{{ entity.label || '未连接主体' }}</strong>
                          <span>{{ `${coordinationEntityConversationCount(entity)} 个来源会话` }}</span>
                        </div>
                        <div class="dossier-coordination-entity-meta">
                          <span>{{ `来源会话：${coordinationEntityBestConversationLabel(entity)}` }}</span>
                          <span>该会话暂未与其他会话形成确认连接</span>
                        </div>
                      </div>
                      <div class="dossier-coordination-entity-actions">
                        <el-button
                          size="small"
                          type="primary"
                          text
                          :disabled="!coordinationEntityHasBestConversation(entity)"
                          @click="handleOpenCoordinationEntityConversation(entity)"
                        >
                          查看来源会话
                        </el-button>
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  v-if="
                    selectedDossierCoordinationPendingNamingEntities.length ||
                    selectedDossierCoordinationPartyNamingTasks.length ||
                    dossierIntelligenceLoading ||
                    dossierIntelligenceState.error
                  "
                  ref="dossierCoordinationNamingRef"
                  class="dossier-coordination-group dossier-coordination-task-section"
                >
                  <div class="dossier-detail-conversations-title">待命名确认</div>
                  <div v-if="dossierIntelligenceLoading" class="dossier-list-status">
                    正在加载命名确认任务...
                  </div>
                  <div
                    v-else-if="
                      dossierIntelligenceState.error &&
                      !selectedDossierCoordinationPartyNamingTasks.length
                    "
                    class="dossier-graph-warning dossier-graph-warning-soft"
                  >
                    {{ dossierIntelligenceState.error }}
                  </div>
                  <div
                    v-else-if="selectedDossierCoordinationPartyNamingTasks.length"
                    class="dossier-intelligence-task-list"
                  >
                    <div
                      v-for="(task, index) in selectedDossierCoordinationPartyNamingTasks"
                      :key="`dossier-party-naming-task-${index}`"
                      class="dossier-intelligence-task-card"
                    >
                      <div class="dossier-intelligence-task-header">
                        <strong>需要命名的当事人</strong>
                        <span class="dossier-intelligence-task-type">{{ task.task_type }}</span>
                      </div>
                      <div class="dossier-intelligence-task-meta">
                        <span>{{ `当前称呼：${task.current_label || '未标注'}` }}</span>
                        <span>
                          {{
                            `建议称呼：${Array.isArray(task.suggested_labels) && task.suggested_labels.length
                              ? task.suggested_labels.join(' / ')
                              : '暂无建议'}`
                          }}
                        </span>
                      </div>
                      <div class="dossier-intelligence-task-reason">
                        {{ task.reason || '暂无原因说明' }}
                      </div>
                      <div class="dossier-intelligence-task-evidence">
                        <span>
                          {{
                            `raw_labels：${Array.isArray(task?.evidence?.raw_labels) && task.evidence.raw_labels.length
                              ? task.evidence.raw_labels.join(' / ')
                              : '暂无'}`
                          }}
                        </span>
                        <span>
                          {{
                            `source_conversation_ids：${formatDossierIntelligenceEvidenceList(task?.evidence?.source_conversation_ids)}`
                          }}
                        </span>
                        <span>
                          {{
                            `raw_node_ids：${formatDossierIntelligenceEvidenceList(task?.evidence?.raw_node_ids)}`
                          }}
                        </span>
                      </div>
                      <div class="dossier-intelligence-task-input">
                        <el-input
                          v-model="dossierPartyNamingDrafts[dossierIntelligenceTaskKey(task)]"
                          maxlength="50"
                          clearable
                          placeholder="请输入该主体在本案中的名称或代称"
                          :disabled="isPartyNamingSubmitting(task)"
                        />
                      </div>
                      <div class="dossier-intelligence-task-actions">
                        <el-button
                          size="small"
                          type="primary"
                          :loading="isPartyNamingSubmitting(task)"
                          :disabled="!canSubmitPartyNaming(task)"
                          @click="handleConfirmPartyNaming(task)"
                        >
                          确认命名
                        </el-button>
                        <span>确认后会写入案卷主体 overlay，并刷新实体连接和图谱预览。</span>
                      </div>
                    </div>
                  </div>
                  <div v-else class="dossier-coordination-empty">暂无待处理命名任务</div>
                </div>
              </template>
            </div>
          </div>

          <div class="dossier-participants-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-toolbar-copy">
                <div class="dossier-detail-conversations-title">主体关系确认</div>
                <span class="dossier-toolbar-subtitle">
                  优先确认不同会话图谱中的实体是否指向同一主体；系统不会自动合并。
                </span>
              </div>
              <div class="dossier-inline-actions">
                <el-button
                  size="small"
                  type="primary"
                  :disabled="!selectedDossierPendingParticipantCount"
                  @click="openParticipantCandidateDialog"
                >
                  处理线索
                </el-button>
                <el-button
                  size="small"
                  plain
                  :loading="dossierParticipantRegistryLoading || dossierParticipantCandidatesLoading"
                  @click="handleRefreshParticipantTasks"
                >
                  刷新
                </el-button>
              </div>
            </div>
            <div class="dossier-participants-hint">
              当前只在案卷内确认跨会话主体关系，不会自动与其他案卷的同名实体合并。
            </div>
            <div class="dossier-participants-hint">
              raw-label 线索和 generic 泛称仍会保留，但不会直接变成已确认主体。
            </div>
            <div class="dossier-participant-summary-grid">
              <div class="dossier-participant-summary-card">
                <span>已确认主体</span>
                <strong>{{ selectedDossierResolvedParticipants.length }}</strong>
              </div>
              <div class="dossier-participant-summary-card">
                <span>跨会话候选</span>
                <strong>{{ selectedDossierCrossConversationCandidates.length }}</strong>
              </div>
              <div class="dossier-participant-summary-card">
                <span>其他主体线索</span>
                <strong>{{ selectedDossierRawLabelCandidates.length }}</strong>
              </div>
            </div>
            <div
              v-if="selectedDossierPendingParticipantCount"
              class="dossier-participant-task-entry"
            >
              <div class="dossier-participant-task-entry-copy">
                <strong>{{ `发现 ${selectedDossierPendingParticipantCount} 条待确认主体线索` }}</strong>
                <span>
                  {{
                    `跨会话 ${selectedDossierCrossConversationCandidates.length} · 其他线索 ${selectedDossierRawLabelCandidates.length}`
                  }}
                </span>
                <span v-if="selectedDossierPendingCandidatePreview.length">
                  {{ `示例：${selectedDossierPendingCandidatePreview.join(' / ')}` }}
                </span>
              </div>
              <div class="dossier-inline-actions">
                <el-button size="small" type="primary" @click="openParticipantCandidateDialog">
                  处理线索
                </el-button>
                <el-button size="small" plain @click="handleOpenParticipantDebugPanel">
                  高级详情
                </el-button>
              </div>
            </div>
            <div v-else class="dossier-participant-task-entry is-empty">
              <div class="dossier-participant-task-entry-copy">
                <strong>暂无待确认主体线索</strong>
                <span>如果刚绑定了新会话或新增了事实，可刷新后重新检查。</span>
              </div>
              <div class="dossier-inline-actions">
                <el-button size="small" plain @click="handleRefreshParticipantTasks">
                  刷新
                </el-button>
                <el-button size="small" plain @click="handleOpenParticipantDebugPanel">
                  高级详情
                </el-button>
              </div>
            </div>
          </div>

          <div class="dossier-resolved-participants-section">
            <el-collapse v-model="dossierAdvancedSections" class="dossier-debug-collapse">
              <el-collapse-item name="advanced">
                <template #title>
                  <div class="dossier-collapse-title">
                    <strong>高级图谱详情 / 调试信息</strong>
                    <span>已确认主体、手动新建主体、节点列表和关系列表默认折叠到这里。</span>
                  </div>
                </template>

                <div class="dossier-resolved-participants-body">
                  <div v-if="dossierParticipantRegistryState.error" class="dossier-graph-error">
                    {{ dossierParticipantRegistryState.error }}
                  </div>
                  <div v-else-if="dossierParticipantRegistryLoading" class="dossier-list-status">
                    正在加载主体确认...
                  </div>
                  <el-collapse v-else v-model="dossierParticipantSections" class="dossier-participant-collapse">
                    <el-collapse-item name="aliases">
                      <template #title>
                        <div class="dossier-collapse-title">
                          <strong>案卷称呼锚点</strong>
                          <span>固定“我 / 对方 / 公司”等常用称呼，便于后续称呼任务更稳定。</span>
                        </div>
                      </template>
                      <div class="dossier-inline-actions dossier-inline-actions-spread">
                        <div class="dossier-participants-hint">
                          这些称呼是当前案卷内的锚点，可使用化名或角色名，不等同于真实姓名。
                        </div>
                        <el-button
                          size="small"
                          type="primary"
                          plain
                          :loading="dossierParticipantsSaving"
                          @click="handleUpdateDossierParticipants"
                        >
                          保存当事人称呼
                        </el-button>
                      </div>
                      <div class="dossier-participants-grid">
                        <el-input
                          v-model="dossierParticipantForm.primary_label"
                          maxlength="100"
                          placeholder="主要当事人称呼，例如：我"
                        />
                        <el-input
                          v-model="dossierParticipantForm.primary_role"
                          maxlength="100"
                          placeholder="主要当事人角色，例如：劳动者"
                        />
                        <el-input
                          v-model="dossierParticipantForm.opposing_label"
                          maxlength="100"
                          placeholder="对方称呼，例如：公司"
                        />
                        <el-input
                          v-model="dossierParticipantForm.opposing_role"
                          maxlength="100"
                          placeholder="对方角色，例如：用人单位"
                        />
                      </div>
                    </el-collapse-item>

                    <el-collapse-item name="participants">
                      <template #title>
                        <div class="dossier-collapse-title">
                          <strong>已确认主体</strong>
                          <span>{{ `共 ${selectedDossierResolvedParticipants.length} 个，可继续编辑显示名、角色和立场。` }}</span>
                        </div>
                      </template>
                      <div class="dossier-resolved-participant-list">
                        <div
                          v-for="participant in selectedDossierResolvedParticipants"
                          :key="participant.id"
                          class="dossier-resolved-participant-card"
                        >
                          <div class="dossier-resolved-participant-grid">
                            <el-input
                              v-model="participant.label"
                              maxlength="100"
                              placeholder="主体称呼"
                            />
                            <el-input
                              v-model="participant.role"
                              maxlength="100"
                              placeholder="角色"
                            />
                            <el-select v-model="participant.party_side" placeholder="立场">
                              <el-option
                                v-for="option in dossierParticipantPartySideOptions"
                                :key="option.value"
                                :label="option.label"
                                :value="option.value"
                              />
                            </el-select>
                            <el-select v-model="participant.entity_type" placeholder="类型">
                              <el-option
                                v-for="option in dossierParticipantEntityTypeOptions"
                                :key="option.value"
                                :label="option.label"
                                :value="option.value"
                              />
                            </el-select>
                          </div>
                          <el-input
                            v-model="participant.description"
                            maxlength="1000"
                            placeholder="备注（可选）"
                          />
                          <div class="dossier-resolved-participant-actions">
                            <span>
                              {{ `已确认 mentions：${selectedDossierParticipantMentionCountMap[participant.id] || 0}` }}
                            </span>
                            <el-button
                              size="small"
                              type="primary"
                              plain
                              :loading="dossierParticipantActionLoading"
                              @click="handleUpdateResolvedParticipant(participant)"
                            >
                              保存主体
                            </el-button>
                          </div>
                        </div>
                        <div v-if="!selectedDossierResolvedParticipants.length" class="dossier-graph-empty">
                          暂无已确认主体，可先处理称呼任务，或手动新建一个主体。
                        </div>
                      </div>
                    </el-collapse-item>

                    <el-collapse-item name="create">
                      <template #title>
                        <div class="dossier-collapse-title">
                          <strong>手动新建主体</strong>
                          <span>只有在确实需要时再创建，不要求真实姓名。</span>
                        </div>
                      </template>
                      <div class="dossier-participant-create-card">
                        <div class="dossier-resolved-participant-grid">
                          <el-input
                            v-model="dossierParticipantCreateForm.label"
                            maxlength="100"
                            placeholder="主体称呼，例如：A公司 / 张三 / 我"
                          />
                          <el-input
                            v-model="dossierParticipantCreateForm.role"
                            maxlength="100"
                            placeholder="角色，例如：用人单位 / HR / 劳动者"
                          />
                          <el-select v-model="dossierParticipantCreateForm.party_side" placeholder="立场">
                            <el-option
                              v-for="option in dossierParticipantPartySideOptions"
                              :key="option.value"
                              :label="option.label"
                              :value="option.value"
                            />
                          </el-select>
                          <el-select v-model="dossierParticipantCreateForm.entity_type" placeholder="类型">
                            <el-option
                              v-for="option in dossierParticipantEntityTypeOptions"
                              :key="option.value"
                              :label="option.label"
                              :value="option.value"
                            />
                          </el-select>
                        </div>
                        <el-input
                          v-model="dossierParticipantCreateForm.description"
                          maxlength="1000"
                          placeholder="备注（可选）"
                        />
                        <div class="dossier-resolved-participant-actions">
                          <span>不要求真实姓名，可使用化名或角色名。</span>
                          <el-button
                            size="small"
                            type="primary"
                            :loading="dossierParticipantActionLoading"
                            @click="handleCreateResolvedParticipant"
                          >
                            新建主体
                          </el-button>
                        </div>
                      </div>
                    </el-collapse-item>

                    <el-collapse-item name="graph">
                      <template #title>
                        <div class="dossier-collapse-title">
                          <strong>图谱节点 / 关系调试详情</strong>
                          <span>默认折叠，仅在需要排查时查看。</span>
                        </div>
                      </template>
                      <div class="dossier-inline-actions dossier-inline-actions-spread">
                        <div class="dossier-graph-hint">
                          当前展示的是案卷下已绑定会话的聚合图谱统计，只读查看，不会改动图谱写入。
                        </div>
                        <el-button
                          size="small"
                          type="primary"
                          plain
                          :loading="dossierGraphLoading"
                          @click="handleRefreshDossierGraph"
                        >
                          刷新图谱状态
                        </el-button>
                      </div>

                      <div v-if="dossierGraphLoading && !dossierGraphLoaded" class="dossier-list-status">
                        正在加载图谱状态...
                      </div>
                      <div v-else-if="dossierGraphState.error" class="dossier-graph-error">
                        {{ dossierGraphState.error }}
                      </div>
                      <div v-else class="dossier-graph-body">
                        <div class="dossier-graph-stats">
                          <div class="dossier-graph-stat">
                            <span class="dossier-graph-stat-label">绑定会话</span>
                            <strong class="dossier-graph-stat-value">
                              {{ dossierGraphState.conversationCount }}
                            </strong>
                          </div>
                          <div class="dossier-graph-stat">
                            <span class="dossier-graph-stat-label">实体节点</span>
                            <strong class="dossier-graph-stat-value">
                              {{ dossierGraphState.nodeCount }}
                            </strong>
                          </div>
                          <div class="dossier-graph-stat">
                            <span class="dossier-graph-stat-label">关系边</span>
                            <strong class="dossier-graph-stat-value">
                              {{ dossierGraphState.edgeCount }}
                            </strong>
                          </div>
                        </div>

                        <div
                          v-if="selectedDossierGraphGeneratedAtLabel || dossierGraphLoading"
                          class="dossier-graph-meta"
                        >
                          <span v-if="selectedDossierGraphGeneratedAtLabel">
                            {{ `最近聚合：${selectedDossierGraphGeneratedAtLabel}` }}
                          </span>
                          <span v-if="dossierGraphLoading">刷新中...</span>
                        </div>

                        <div v-if="dossierGraphState.warning" class="dossier-graph-warning">
                          {{ dossierGraphState.warning }}
                        </div>
                        <div v-if="selectedDossierPendingParticipantCount" class="dossier-graph-warning dossier-graph-warning-soft">
                          {{ `有 ${selectedDossierPendingParticipantCount} 条主体线索待确认，确认后图谱会更清晰。` }}
                        </div>

                        <div class="dossier-graph-aliases">
                          <div class="dossier-graph-alias-item">
                            <span>主要当事人</span>
                            <strong>{{ selectedDossierGraphPrimaryAlias }}</strong>
                          </div>
                          <div class="dossier-graph-alias-item">
                            <span>对方</span>
                            <strong>{{ selectedDossierGraphOpposingAlias }}</strong>
                          </div>
                        </div>

                        <div class="dossier-graph-preview">
                          <div class="dossier-graph-preview-header">
                            <div class="dossier-detail-conversations-title">图谱预览</div>
                            <span
                              v-if="selectedDossierGraphPreviewTruncated"
                              class="dossier-graph-preview-note"
                            >
                              仅展示部分图谱
                            </span>
                          </div>
                          <div class="dossier-graph-preview-hint">
                            节点和关系来自案卷绑定会话中的结构化案情，可能包含系统推断的未确认关系。
                          </div>
                          <div class="dossier-graph-preview-hint">
                            同名“我 / 公司 / 对方”只在当前案卷内按用户确认称呼展示，不会自动与其他案卷同名主体合并。
                          </div>

                          <div v-if="selectedDossierGraphHasPreview" class="dossier-graph-preview-body">
                            <div class="dossier-graph-preview-section">
                              <div class="dossier-graph-preview-subtitle">节点</div>
                              <div class="dossier-graph-node-chips">
                                <div
                                  v-for="node in selectedDossierGraphPreviewNodes"
                                  :key="node.id"
                                  class="dossier-graph-node-chip"
                                  :title="formatGraphPropertiesTitle(node.properties)"
                                >
                                  <strong>{{ graphNodePrimaryLabel(node) }}</strong>
                                  <span>{{ formatGraphPreviewNodeKind(node) }}</span>
                                  <span v-if="node.confirmed">已确认主体</span>
                                  <span v-else-if="node.pending_confirmation">待确认</span>
                                  <span v-if="graphNodeRawLabelsSummary(node)">
                                    {{ `原始标签：${graphNodeRawLabelsSummary(node)}` }}
                                  </span>
                                  <span>
                                    {{ `来源：${formatGraphConversationListLabel(graphNodeConversationIds(node))}` }}
                                  </span>
                                </div>
                              </div>
                            </div>

                            <div class="dossier-graph-preview-section">
                              <div class="dossier-graph-preview-subtitle">关系</div>
                              <div v-if="selectedDossierGraphPreviewEdges.length" class="dossier-graph-edge-list">
                                <div
                                  v-for="edge in selectedDossierGraphPreviewEdges"
                                  :key="edge.id"
                                  class="dossier-graph-edge-item"
                                  :title="formatGraphPropertiesTitle(edge.properties)"
                                >
                                  <span class="dossier-graph-edge-source">{{ edge.sourceLabel }}</span>
                                  <span class="dossier-graph-edge-relation">{{ edge.relationLabel }}</span>
                                  <span class="dossier-graph-edge-target">{{ edge.targetLabel }}</span>
                                  <span class="dossier-graph-edge-meta">
                                    {{ formatGraphConversationListLabel(edge.conversationIds) }}
                                  </span>
                                </div>
                              </div>
                              <div v-else class="dossier-graph-preview-empty">
                                暂无关系边预览
                              </div>
                            </div>

                            <div
                              v-if="selectedDossierGraphPreviewTruncated"
                              class="dossier-graph-preview-truncation"
                            >
                              仅展示前 12 个节点和前 20 条关系，完整数据已聚合。
                            </div>
                          </div>
                          <div v-else class="dossier-graph-preview-empty">
                            {{ selectedDossierGraphEmptyText || '暂无图谱数据' }}
                          </div>
                        </div>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>

          <div class="dossier-detail-conversations">
            <div class="dossier-detail-conversations-title">绑定会话</div>
            <el-empty
              v-if="!selectedDossierDetail.conversations?.length"
              description="当前案卷还没有绑定会话"
              :image-size="52"
            />
            <div v-else class="dossier-conversation-list">
              <div
                v-for="conversation in selectedDossierDetail.conversations"
                :key="conversation.id"
                class="dossier-conversation-item"
              >
                <div class="dossier-conversation-top">
                  <span class="dossier-conversation-title">
                    {{ conversation.title || `会话 #${conversation.id}` }}
                  </span>
                  <span class="dossier-conversation-time">
                    {{ formatDossierConversationTime(conversation.updated_at) }}
                  </span>
                </div>
                <div class="dossier-conversation-tags">
                  <el-tag size="small" effect="light" :type="conversationModeTagType(conversation.conversation_mode)">
                    {{ conversationModeLabel(conversation.conversation_mode) }}
                  </el-tag>
                  <el-tag size="small" effect="plain">
                    {{ persistenceConsentLabel(conversation.persistence_consent) }}
                  </el-tag>
                  <el-tag v-if="conversation.case_type" size="small" effect="plain">
                    {{ conversation.case_type }}
                  </el-tag>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>

  <el-dialog
    v-model="dossierParticipantCandidateDialogVisible"
    title="主体关系确认"
    width="720px"
  >
    <div class="dossier-participants-hint">
      这些卡片代表案卷内待确认的主体线索。系统优先展示跨会话主体同一性候选，不会自动合并。
    </div>
    <div
      v-if="selectedDossierRawLabelCandidates.length"
      class="dossier-participants-hint"
    >
      {{ `另外 ${selectedDossierRawLabelCandidates.length} 条 raw-label 线索已降级到高级/调试详情，不作为普通用户主确认任务。` }}
    </div>
    <div
      v-if="selectedDossierParticipantCandidates.length"
      class="dossier-participant-task-list"
    >
      <div
        v-for="candidate in selectedDossierParticipantCandidates"
        :key="candidateKey(candidate)"
        class="dossier-participant-task-card"
      >
        <div class="dossier-participant-task-header">
          <div class="dossier-participant-task-copy">
            <strong>{{ candidateCardTitle(candidate) }}</strong>
            <span>{{ candidateCardSubtitle(candidate) }}</span>
          </div>
          <div class="dossier-participant-task-tags">
            <el-tag
              v-if="isCrossConversationCandidate(candidate)"
              size="small"
              type="success"
            >
              跨会话
            </el-tag>
            <el-tag size="small" :type="candidateLabelKindTagType(candidate)">
              {{ candidateLabelKindLabel(candidate) }}
            </el-tag>
            <el-tag
              v-if="candidateConfidenceLabel(candidate)"
              size="small"
              effect="plain"
            >
              {{ `置信度 ${candidateConfidenceLabel(candidate)}` }}
            </el-tag>
            <el-tag size="small" effect="plain">
              {{ `${candidate.mentions?.length || 0} mentions` }}
            </el-tag>
          </div>
        </div>
        <div class="dossier-participant-task-description">
          {{ candidateIntroText(candidate) }}
        </div>
        <div class="dossier-participant-task-meta">
          {{ candidate.reason || '系统未提供额外说明。' }}
        </div>

        <template v-if="isCrossConversationCandidate(candidate)">
          <div class="dossier-participant-resolution-pair">
            <div class="dossier-participant-resolution-node">
              <span class="dossier-participant-resolution-node-title">来源节点</span>
              <strong>{{ formatGraphNodeLabel(candidate.source_node) }}</strong>
              <span>{{ formatGraphConversationLabel(candidate.source_node?.conversation_id) }}</span>
              <span>{{ `原始称呼：${candidate.source_node?.raw_label || formatGraphNodeLabel(candidate.source_node)}` }}</span>
              <span>{{ `节点类型：${formatGraphNodeType(candidate.source_node?.graph_node_type || candidate.source_node?.entity_type)}` }}</span>
            </div>
            <div class="dossier-participant-resolution-arrow">⇄</div>
            <div class="dossier-participant-resolution-node">
              <span class="dossier-participant-resolution-node-title">候选节点</span>
              <strong>{{ formatGraphNodeLabel(candidate.target_node) }}</strong>
              <span>{{ formatGraphConversationLabel(candidate.target_node?.conversation_id) }}</span>
              <span>{{ `原始称呼：${candidate.target_node?.raw_label || formatGraphNodeLabel(candidate.target_node)}` }}</span>
              <span>{{ `节点类型：${formatGraphNodeType(candidate.target_node?.graph_node_type || candidate.target_node?.entity_type)}` }}</span>
            </div>
          </div>

          <div
            v-if="candidateRecommendedParticipantLabel(candidate)"
            class="dossier-participant-task-meta"
          >
            {{ `系统建议归入：${candidateRecommendedParticipantLabel(candidate)}` }}
          </div>
          <div
            v-if="candidateActionBlockedReason(candidate)"
            class="dossier-participant-task-meta"
          >
            {{ `当前不能直接确认：${candidateActionBlockedReason(candidate)}` }}
          </div>

          <el-select
            v-if="candidate.can_link_existing && selectedDossierResolvedParticipants.length"
            v-model="dossierParticipantCandidateSelections[candidateKey(candidate)]"
            clearable
            placeholder="如需改绑到其他已有主体，请手动选择"
          >
            <el-option
              v-for="participant in selectedDossierResolvedParticipants"
              :key="participant.id"
              :label="participantDisplayLabel(participant)"
              :value="participant.id"
            />
          </el-select>
        </template>

        <template v-else>
          <div class="dossier-participant-task-meta">
            {{ `提及会话：${candidateMentionsConversationLabel(candidate)}` }}
          </div>
        </template>

        <div
          v-if="candidateMatchCandidates(candidate).length"
          class="dossier-participant-task-match-list"
        >
          <div class="dossier-participant-task-match-title">候选已有主体</div>
          <div class="dossier-participant-task-match-chips">
            <button
              v-for="match in candidateMatchCandidates(candidate)"
              :key="`${candidateKey(candidate)}-match-${match.participant_id}`"
              type="button"
              class="dossier-participant-match-chip"
              :class="{ selected: dossierParticipantCandidateSelections[candidateKey(candidate)] === match.participant_id }"
              @click="handleUseMatchCandidate(candidate, match.participant_id)"
            >
              <strong>{{ matchCandidateDisplayLabel(match) }}</strong>
              <span>{{ match.reason || match.match_type || '候选映射' }}</span>
            </button>
          </div>
        </div>

        <template v-if="isCrossConversationCandidate(candidate)">
          <div
            v-if="isParticipantCandidateCreateOpen(candidate)"
            class="dossier-participant-inline-create-card"
          >
            <div class="dossier-resolved-participant-grid">
              <el-input
                v-model="dossierParticipantCandidateCreateDraft.label"
                maxlength="100"
                placeholder="独立主体称呼"
              />
              <el-input
                v-model="dossierParticipantCandidateCreateDraft.role"
                maxlength="100"
                placeholder="角色（可选）"
              />
              <el-select
                v-model="dossierParticipantCandidateCreateDraft.party_side"
                placeholder="立场"
              >
                <el-option
                  v-for="option in dossierParticipantPartySideOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
              <el-select
                v-model="dossierParticipantCandidateCreateDraft.entity_type"
                placeholder="类型"
              >
                <el-option
                  v-for="option in dossierParticipantEntityTypeOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </div>
            <div class="dossier-participant-task-actions">
              <span>只有确认这两个会话中的实体应归入新主体时才创建。</span>
              <div class="dossier-inline-actions">
                <el-button size="small" @click="resetParticipantCandidateCreateDraft">
                  取消
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  :loading="dossierParticipantActionLoading"
                  @click="handleResolveParticipantCandidateCreate(candidate)"
                >
                  确认新建
                </el-button>
              </div>
            </div>
          </div>

          <div class="dossier-participant-task-actions">
            <el-button
              size="small"
              type="primary"
              plain
              :disabled="!crossCandidateExistingParticipantId(candidate) && !canCreateParticipantFromCandidate(candidate)"
              :loading="dossierParticipantActionLoading"
              @click="handleResolveCrossConversationCandidate(candidate)"
            >
              是，同一个主体
            </el-button>
            <el-button
              v-if="canCreateParticipantFromCandidate(candidate)"
              size="small"
              type="primary"
              :loading="dossierParticipantActionLoading"
              @click="startParticipantCandidateCreate(candidate)"
            >
              新建为独立主体
            </el-button>
            <el-button size="small" @click="handleRejectCrossConversationCandidate(candidate)">
              不是同一个
            </el-button>
            <el-button size="small" @click="handleIgnoreParticipantCandidate(candidate)">
              暂不处理
            </el-button>
          </div>
        </template>

        <template v-else-if="candidateLabelKind(candidate) === 'specific'">
          <el-select
            v-if="candidate.can_link_existing && selectedDossierResolvedParticipants.length"
            v-model="dossierParticipantCandidateSelections[candidateKey(candidate)]"
            clearable
            placeholder="先选择要映射的已有主体"
          >
            <el-option
              v-for="participant in selectedDossierResolvedParticipants"
              :key="participant.id"
              :label="participantDisplayLabel(participant)"
              :value="participant.id"
            />
          </el-select>

          <div
            v-if="isParticipantCandidateCreateOpen(candidate)"
            class="dossier-participant-inline-create-card"
          >
            <div class="dossier-resolved-participant-grid">
              <el-input
                v-model="dossierParticipantCandidateCreateDraft.label"
                maxlength="100"
                placeholder="独立主体称呼"
              />
              <el-input
                v-model="dossierParticipantCandidateCreateDraft.role"
                maxlength="100"
                placeholder="角色（可选）"
              />
              <el-select
                v-model="dossierParticipantCandidateCreateDraft.party_side"
                placeholder="立场"
              >
                <el-option
                  v-for="option in dossierParticipantPartySideOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
              <el-select
                v-model="dossierParticipantCandidateCreateDraft.entity_type"
                placeholder="类型"
              >
                <el-option
                  v-for="option in dossierParticipantEntityTypeOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </div>
            <div class="dossier-participant-task-actions">
              <span>只有确认它是独立主体时才新建。</span>
              <div class="dossier-inline-actions">
                <el-button size="small" @click="resetParticipantCandidateCreateDraft">
                  取消
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  :loading="dossierParticipantActionLoading"
                  @click="handleResolveParticipantCandidateCreate(candidate)"
                >
                  确认新建
                </el-button>
              </div>
            </div>
          </div>

          <div class="dossier-participant-task-actions">
            <el-button
              size="small"
              type="primary"
              plain
              :disabled="!dossierParticipantCandidateSelections[candidateKey(candidate)]"
              :loading="dossierParticipantActionLoading"
              @click="handleResolveSpecificCandidateToExisting(candidate)"
            >
              是，同一个
            </el-button>
            <el-button
              v-if="canCreateParticipantFromCandidate(candidate)"
              size="small"
              type="primary"
              :loading="dossierParticipantActionLoading"
              @click="startParticipantCandidateCreate(candidate)"
            >
              新建为独立主体
            </el-button>
            <el-button size="small" @click="handleIgnoreParticipantCandidate(candidate)">
              暂不处理
            </el-button>
          </div>
        </template>

        <template v-else>
          <el-select
            v-if="selectedDossierOpponentParticipants.length > 1"
            v-model="dossierParticipantCandidateSelections[candidateKey(candidate)]"
            clearable
            placeholder="如需指向对方，请先选择一个对方主体"
          >
            <el-option
              v-for="participant in selectedDossierOpponentParticipants"
              :key="participant.id"
              :label="participantDisplayLabel(participant)"
              :value="participant.id"
            />
          </el-select>

          <div class="dossier-participant-task-actions">
            <el-button
              size="small"
              type="primary"
              plain
              :loading="dossierParticipantActionLoading"
              @click="handleResolveGenericCandidateToSelf(candidate)"
            >
              指我
            </el-button>
            <el-button
              size="small"
              type="primary"
              plain
              :loading="dossierParticipantActionLoading"
              @click="handleResolveGenericCandidateToOpponent(candidate)"
            >
              指对方
            </el-button>
            <el-button size="small" @click="handleMarkNonEntityCandidate(candidate)">
              不是具体主体
            </el-button>
            <el-button size="small" @click="handleIgnoreParticipantCandidate(candidate)">
              暂不处理
            </el-button>
          </div>
        </template>
        </div>
      </div>
    <div v-else class="dossier-graph-empty">
      {{
        selectedDossierRawLabelCandidates.length
          ? '当前没有待确认的跨会话主体关系。其他 raw-label 线索已放入高级/调试详情。'
          : '当前没有待确认的主体线索。'
      }}
    </div>

    <template #footer>
      <el-button @click="dossierParticipantCandidateDialogVisible = false">
        关闭
      </el-button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="trackedOnboardingVisible"
    title="案卷初始化"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
  >
    <div class="tracked-onboarding-copy">
      <div class="tracked-onboarding-title">为了后续持续跟踪，请确认案卷和当事人称呼。</div>
      <div class="tracked-onboarding-subtitle">
        你可以使用化名或角色名，不需要填写真实姓名。
      </div>
      <div class="tracked-onboarding-subtitle">
        当前聊天记录仍会保存在会话历史中；选择持续跟踪后，系统会把案情整理为可持续维护的结构化案情。
      </div>
    </div>

    <div class="tracked-onboarding-form">
      <el-input
        v-model="trackedOnboardingForm.title"
        maxlength="255"
        placeholder="案卷名称"
      />
      <div class="tracked-onboarding-grid">
        <el-input
          v-model="trackedOnboardingForm.primary_label"
          maxlength="100"
          placeholder="主要当事人称呼，例如：我"
        />
        <el-input
          v-model="trackedOnboardingForm.primary_role"
          maxlength="100"
          placeholder="主要当事人角色，例如：劳动者"
        />
        <el-input
          v-model="trackedOnboardingForm.opposing_label"
          maxlength="100"
          placeholder="对方称呼，例如：公司"
        />
        <el-input
          v-model="trackedOnboardingForm.opposing_role"
          maxlength="100"
          placeholder="对方角色，例如：用人单位"
        />
      </div>
      <div class="tracked-onboarding-hint">
        {{
          trackedOnboardingState.dossierId
            ? '当前会话已绑定案卷，本次会更新该案卷的名称和当事人称呼。'
            : '当前会为这次持续跟踪创建一个新案卷，并把本会话绑定到该案卷。'
        }}
      </div>
    </div>

    <template #footer>
      <div class="tracked-onboarding-actions">
        <el-button @click="handleCancelTrackedOnboarding" :disabled="trackedOnboardingSubmitting">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="trackedOnboardingSubmitting"
          @click="handleConfirmTrackedOnboarding"
        >
          确认并继续
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  CircleCheckFilled,
  Delete,
  Document,
  Download,
  Loading,
  Promotion,
  Service,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AnalysisPanel from './components/AnalysisPanel.vue'
import ChatMessage from './components/ChatMessage.vue'
import SessionList from './components/SessionList.vue'
import {
  archiveDossier,
  attachConversationToDossier,
  batchDeleteConversations,
  clearConversationMessages,
  confirmDossierPartyNaming,
  confirmDossierSelfIdentity,
  createConversation,
  createDossier,
  createDossierParticipant,
  deleteConversation,
  generateDossierSummary,
  getConversationGraph,
  getConversationMessages,
  getConversationSummary,
  getConversations,
  getDossierCoordinationGraph,
  getDossierDetail,
  getDossierDisplayGraph,
  getDossierIntelligence,
  getDossierGraph,
  getDossierParticipantCandidates,
  getDossierParticipants,
  getDossiers,
  hardDeleteDossier,
  getLocalModelStatus,
  resolveDossierParticipants,
  searchConversations,
  getSystemStatus,
  sendMessage,
  setConversationModeConsent,
  setLocalModelStatus,
  submitFeedback,
  updateDossier,
  updateDossierParticipant,
  updateDossierParticipants,
  updateConversationTitle,
} from './api/index.js'
import { clearAuthSession, getStoredUser } from './utils/auth.js'

const router = useRouter()

const conversations = ref([])
const currentConversationId = ref(null)
const currentSessionId = ref('')
const messages = ref([])
const inputText = ref('')
const proactiveGraphMode = ref(true)
const sending = ref(false)
const summaryLoading = ref(false)
const scrollRef = ref(null)
const messageListRef = ref(null)
const statusTimer = ref(null)
const graphRefreshTimer = ref(null)
const graphRefreshStopTimer = ref(null)
const graphSidebarCollapsed = ref(false)
const latestGraphRequestId = ref(0)
const conversationListLoading = ref(false)
const conversationSearchKeyword = ref('')
const sessionBatchMode = ref(false)
const dossiers = ref([])
const dossierDrawerVisible = ref(false)
const dossierListLoading = ref(false)
const dossierSearchKeyword = ref('')
const dossierStatusFilter = ref('all')
const dossierSaving = ref(false)
const dossierBindingId = ref(null)
const DOSSIER_GRAPH_PREVIEW_NODE_LIMIT = 12
const DOSSIER_GRAPH_PREVIEW_EDGE_LIMIT = 20
const selectedDossierId = ref(null)
const selectedDossierDetail = ref(null)
const dossierDetailLoading = ref(false)
const dossierDetailSaving = ref(false)
const dossierParticipantsSaving = ref(false)
const dossierParticipantRegistryLoading = ref(false)
const dossierParticipantCandidatesLoading = ref(false)
const dossierParticipantActionLoading = ref(false)
const dossierAdvancedSections = ref([])
const dossierParticipantSections = ref([])
const dossierParticipantCandidateDialogVisible = ref(false)
const dossierParticipantCandidateCreateKey = ref('')
const dossierSummaryGenerating = ref(false)
const dossierGraphLoading = ref(false)
const dossierGraphLoaded = ref(false)
const dossierCoordinationGraphLoading = ref(false)
const dossierIntelligenceLoading = ref(false)
const dossierPartyNamingSubmittingKey = ref('')
const dossierCoordinationNamingRef = ref(null)
const dossierSelfIdentityTaskStates = reactive({})
const trackedOnboardingVisible = ref(false)
const trackedOnboardingSubmitting = ref(false)
const dossierGraphState = reactive({
  dossierId: null,
  conversationCount: 0,
  nodeCount: 0,
  edgeCount: 0,
  nodes: [],
  edges: [],
  view: 'raw',
  generatedAt: '',
  warning: '',
  error: '',
  participantAliases: {},
})
const dossierParticipantRegistryState = reactive({
  participants: [],
  mentions: [],
  candidates: [],
  error: '',
})
const dossierIntelligenceState = reactive({
  dossierId: null,
  summary: {
    text: '',
    source: '',
    updatedAt: '',
  },
  confirmationTasks: [],
  displayGraphNotes: [],
  provider: '',
  model: '',
  fallbackReason: '',
  traceSteps: [],
  traceWarnings: [],
  error: '',
})
const dossierCoordinationGraphState = reactive({
  dossierId: null,
  entities: [],
  edges: [],
  tasks: [],
  stats: {
    boundConversations: 0,
    connectedEntities: 0,
    pendingIdentity: 0,
    pendingNaming: 0,
    isolatedAnchors: 0,
  },
  view: 'coordination',
  generatedAt: '',
  error: '',
})
const dossierPartyNamingDrafts = reactive({})
const dossierForm = reactive({
  title: '',
  case_type: '',
  summary: '',
})
const dossierDetailForm = reactive({
  title: '',
  case_type: '',
  summary: '',
  status: 'active',
})
const dossierParticipantForm = reactive({
  primary_label: '',
  primary_role: '',
  opposing_label: '',
  opposing_role: '',
})
const dossierParticipantCreateForm = reactive({
  label: '',
  role: '',
  party_side: 'unknown',
  entity_type: 'unknown',
  description: '',
})
const dossierParticipantCandidateCreateDraft = reactive({
  label: '',
  role: '',
  party_side: 'unknown',
  entity_type: 'unknown',
  description: '',
})
const trackedOnboardingForm = reactive({
  title: '',
  primary_label: '',
  primary_role: '',
  opposing_label: '',
  opposing_role: '',
})
const trackedOnboardingState = reactive({
  conversationId: null,
  text: '',
  forceCurrentCase: false,
  draftText: '',
  dossierId: null,
})
const dossierParticipantCandidateSelections = reactive({})
const dossierParticipantCandidateDismissed = reactive({})
const dossierParticipantPartySideOptions = [
  { label: '主张方', value: 'claimant' },
  { label: '对方', value: 'opponent' },
  { label: '中立', value: 'neutral' },
  { label: '未分类', value: 'unknown' },
]
const dossierParticipantEntityTypeOptions = [
  { label: '自然人', value: 'person' },
  { label: '组织', value: 'organization' },
  { label: '机构', value: 'authority' },
  { label: '未分类', value: 'unknown' },
]
let trackedOnboardingResolver = null
const localModel = reactive({
  enabled: false,
  healthy: false,
  vllmUrl: '',
  loading: false,
})

const systemStatus = reactive({
  status: 'loading',
  ready: false,
  healthy: false,
  ragReady: false,
  docCount: 0,
  model: '',
  tools: [],
  components: {
    chroma: 'loading',
    neo4j: 'loading',
    llm: 'loading',
    celery: 'loading',
  },
})

const currentAnalysis = reactive({
  actions: [],
  sources: [],
  routeIntent: '',
  latencyMs: null,
  confidenceScore: null,
  missingInfo: [],
  requestState: 'idle',
  requestError: '',
  updatedAt: '',
  graphSyncStatus: 'idle',
  graphSyncSummary: '',
  graphSyncUpdatedAt: '',
})
const currentGraph = reactive({
  query: '',
  nodes: [],
  edges: [],
  tokens: [],
  generatedAt: '',
  loading: false,
  error: '',
})

const currentUsername = computed(() => getStoredUser()?.username || '已登录用户')
const currentConversation = computed(() =>
  conversations.value.find((item) => item.id === currentConversationId.value) || null
)
const currentConversationModeLabel = computed(() =>
  conversationModeLabel(currentConversation.value?.conversation_mode)
)
const currentConversationModeTagType = computed(() =>
  conversationModeTagType(currentConversation.value?.conversation_mode)
)
const currentConversationDossier = computed(() => {
  const dossierId = currentConversation.value?.dossier_id
  if (!dossierId) return null
  return (
    dossiers.value.find((item) => item.id === dossierId) || {
      id: dossierId,
      title: `案卷 #${dossierId}`,
      case_type: '',
      summary: '',
      status: 'active',
    }
  )
})
const currentConversationDossierLabel = computed(() => {
  const dossier = currentConversationDossier.value
  if (!dossier) return ''
  return dossier.title || `案卷 #${dossier.id}`
})
const selectedDossierConversationCount = computed(
  () => selectedDossierDetail.value?.conversation_count || 0
)
const filteredDossiers = computed(() => {
  const keyword = String(dossierSearchKeyword.value || '').trim().toLowerCase()
  const statusFilter = String(dossierStatusFilter.value || 'all').trim().toLowerCase()

  return dossiers.value.filter((dossier) => {
    const dossierStatus = String(dossier?.status || 'active').toLowerCase()
    if (statusFilter === 'active' && dossierStatus !== 'active') return false
    if (statusFilter === 'archived' && dossierStatus !== 'archived') return false

    if (!keyword) return true
    const haystack = [
      dossier?.title,
      dossier?.case_type,
      dossier?.summary,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return haystack.includes(keyword)
  })
})
const dossierListEmptyDescription = computed(() => {
  if (!dossiers.value.length) {
    return '创建第一个案卷，用于持续跟踪同一法律问题'
  }
  return '没有找到符合条件的案卷'
})
const selectedDossierResolvedParticipants = computed(() =>
  Array.isArray(dossierParticipantRegistryState.participants)
    ? dossierParticipantRegistryState.participants
    : []
)
const selectedDossierParticipantMentions = computed(() =>
  Array.isArray(dossierParticipantRegistryState.mentions)
    ? dossierParticipantRegistryState.mentions
    : []
)
const selectedDossierAllParticipantCandidates = computed(() =>
  Array.isArray(dossierParticipantRegistryState.candidates)
    ? sortParticipantCandidates(
        dossierParticipantRegistryState.candidates.filter(
          (candidate) => !dossierParticipantCandidateDismissed[candidateKey(candidate)]
        )
      )
    : []
)
const selectedDossierParticipantCandidates = computed(() =>
  selectedDossierAllParticipantCandidates.value.filter((candidate) => isCrossConversationCandidate(candidate))
)
const selectedDossierPendingParticipantCount = computed(
  () => selectedDossierParticipantCandidates.value.length
)
const selectedDossierCrossConversationCandidates = computed(() =>
  selectedDossierParticipantCandidates.value
)
const selectedDossierRawLabelCandidates = computed(() =>
  selectedDossierAllParticipantCandidates.value.filter((candidate) => !isCrossConversationCandidate(candidate))
)
const selectedDossierSpecificParticipantCandidates = computed(() =>
  selectedDossierAllParticipantCandidates.value.filter(
    (candidate) => String(candidate?.label_kind || '').trim().toLowerCase() === 'specific'
  )
)
const selectedDossierGenericParticipantCandidates = computed(() =>
  selectedDossierAllParticipantCandidates.value.filter(
    (candidate) => String(candidate?.label_kind || '').trim().toLowerCase() === 'generic'
  )
)
const selectedDossierPendingCandidatePreview = computed(() =>
  selectedDossierParticipantCandidates.value
    .slice(0, 4)
    .map((candidate) => candidateRawLabel(candidate))
    .filter(Boolean)
)
const selectedDossierParticipantMentionCountMap = computed(() => {
  const lookup = {}
  for (const mention of selectedDossierParticipantMentions.value) {
    const participantId = Number(mention?.participant_id || 0)
    if (!participantId) continue
    lookup[participantId] = (lookup[participantId] || 0) + 1
  }
  return lookup
})
const selectedDossierGraphParticipantAliases = computed(() => {
  const aliases = dossierGraphState.participantAliases
  if (aliases && Object.keys(aliases).length) {
    return aliases
  }
  return selectedDossierDetail.value?.participant_aliases || {}
})
const selectedDossierGraphGeneratedAtLabel = computed(() =>
  formatDossierConversationTime(dossierGraphState.generatedAt)
)
const selectedDossierGraphPrimaryAlias = computed(() =>
  formatParticipantAliasSummary(selectedDossierGraphParticipantAliases.value?.primary_party)
)
const selectedDossierGraphOpposingAlias = computed(() =>
  formatParticipantAliasSummary(selectedDossierGraphParticipantAliases.value?.opposing_party)
)
const selectedDossierIntelligenceSummary = computed(() => dossierIntelligenceState.summary || {})
const selectedDossierIntelligenceConfirmationTasks = computed(() =>
  Array.isArray(dossierIntelligenceState.confirmationTasks)
    ? dossierIntelligenceState.confirmationTasks
    : []
)
const selectedDossierIntelligenceDisplayGraphNotes = computed(() =>
  Array.isArray(dossierIntelligenceState.displayGraphNotes)
    ? dossierIntelligenceState.displayGraphNotes
    : []
)
const selectedDossierIntelligenceTraceSteps = computed(() =>
  Array.isArray(dossierIntelligenceState.traceSteps) ? dossierIntelligenceState.traceSteps : []
)
const selectedDossierIntelligenceTraceWarnings = computed(() =>
  Array.isArray(dossierIntelligenceState.traceWarnings)
    ? dossierIntelligenceState.traceWarnings
    : []
)
const selectedDossierCoordinationEntities = computed(() =>
  Array.isArray(dossierCoordinationGraphState.entities) ? dossierCoordinationGraphState.entities : []
)
const selectedDossierCoordinationTasks = computed(() =>
  Array.isArray(dossierCoordinationGraphState.tasks) ? dossierCoordinationGraphState.tasks : []
)
const selectedDossierCoordinationStats = computed(() => ({
  boundConversations: Number(dossierCoordinationGraphState.stats?.boundConversations || 0),
  connectedEntities: Number(dossierCoordinationGraphState.stats?.connectedEntities || 0),
  pendingIdentity: Number(dossierCoordinationGraphState.stats?.pendingIdentity || 0),
  pendingNaming: Number(dossierCoordinationGraphState.stats?.pendingNaming || 0),
  isolatedAnchors: Number(dossierCoordinationGraphState.stats?.isolatedAnchors || 0),
}))
const selectedDossierCoordinationConfirmedEntities = computed(() =>
  selectedDossierCoordinationEntities.value.filter(
    (entity) => String(entity?.status || '').trim() === 'confirmed'
  )
)
const selectedDossierCoordinationPendingIdentityEntities = computed(() =>
  selectedDossierCoordinationEntities.value.filter(
    (entity) => String(entity?.status || '').trim() === 'pending_identity'
  )
)
const selectedDossierCoordinationPendingNamingEntities = computed(() =>
  selectedDossierCoordinationEntities.value.filter(
    (entity) => String(entity?.status || '').trim() === 'pending_naming'
  )
)
const selectedDossierCoordinationIsolatedAnchorEntities = computed(() =>
  selectedDossierCoordinationEntities.value.filter(
    (entity) => String(entity?.status || '').trim() === 'isolated_anchor'
  )
)
const selectedDossierCoordinationIsEmpty = computed(
  () => selectedDossierCoordinationEntities.value.length === 0
)
const selectedDossierCoordinationIdentityTaskLookup = computed(() => {
  const lookup = {}
  for (const task of selectedDossierCoordinationTasks.value) {
    if (String(task?.type || '').trim() !== 'entity_identity') continue
    const entityIds = Array.isArray(task?.entity_ids) ? task.entity_ids : []
    for (const entityId of entityIds) {
      const normalizedId = String(entityId || '').trim()
      if (!normalizedId) continue
      lookup[normalizedId] = task
    }
  }
  return lookup
})
const selectedDossierCoordinationPartyNamingTasks = computed(() =>
  selectedDossierIntelligenceConfirmationTasks.value.filter(
    (task) => String(task?.task_type || '').trim() === 'party_naming'
  )
)
const selectedDossierSelfAnchorIdentityTasks = computed(() =>
  selectedDossierIntelligenceConfirmationTasks.value.filter(
    (task) =>
      String(task?.task_type || '').trim() === 'entity_identity' &&
      String(task?.subject || '').trim() === 'self_anchor' &&
      !selfIdentityTaskIsDismissed(task)
  )
)
const selectedDossierSelfAnchorParticipants = computed(() =>
  selectedDossierResolvedParticipants.value.filter(
    (participant) => String(participant?.label || '').trim() === '我'
  )
)
const selectedDossierOpponentParticipants = computed(() =>
  selectedDossierResolvedParticipants.value.filter(
    (participant) => String(participant?.party_side || '').trim() === 'opponent'
  )
)
const selectedDossierGraphIsEmpty = computed(
  () => dossierGraphLoaded.value && dossierGraphState.nodeCount === 0 && dossierGraphState.edgeCount === 0
)
const selectedDossierGraphPreviewNodes = computed(() =>
  Array.isArray(dossierGraphState.nodes)
    ? dossierGraphState.nodes.slice(0, DOSSIER_GRAPH_PREVIEW_NODE_LIMIT)
    : []
)
const selectedDossierGraphNodeLookup = computed(() => {
  const lookup = {}
  for (const node of Array.isArray(dossierGraphState.nodes) ? dossierGraphState.nodes : []) {
    const nodeId = String(node?.id || '').trim()
    if (!nodeId) continue
    lookup[nodeId] = node
  }
  return lookup
})
const selectedDossierGraphPreviewEdges = computed(() => {
  const lookup = selectedDossierGraphNodeLookup.value
  const edges = Array.isArray(dossierGraphState.edges)
    ? dossierGraphState.edges.slice(0, DOSSIER_GRAPH_PREVIEW_EDGE_LIMIT)
    : []

  return edges.map((edge) => {
    const sourceId = String(edge?.source || '').trim()
    const targetId = String(edge?.target || '').trim()
    const sourceNode = lookup[sourceId] || null
    const targetNode = lookup[targetId] || null
    const properties = edge?.properties && typeof edge.properties === 'object' ? edge.properties : {}

    return {
      ...edge,
      sourceLabel: graphNodePrimaryLabel(sourceNode) || sourceId || 'Unknown',
      targetLabel: graphNodePrimaryLabel(targetNode) || targetId || 'Unknown',
      relationLabel: String(edge?.label || edge?.type || 'CASE_RELATION').trim() || 'CASE_RELATION',
      conversationIds: [
        ...normalizeGraphConversationIds(edge?.conversation_ids),
        ...normalizeGraphConversationIds(edge?.source_conversation_ids),
        ...normalizeGraphConversationIds(edge?.target_conversation_ids),
        ...normalizeGraphConversationIds(properties.conversation_id),
        ...graphNodeConversationIds(sourceNode),
        ...graphNodeConversationIds(targetNode),
      ].filter((value, index, array) => array.indexOf(value) === index),
    }
  })
})
const selectedDossierGraphHasPreview = computed(
  () =>
    selectedDossierGraphPreviewNodes.value.length > 0 ||
    selectedDossierGraphPreviewEdges.value.length > 0
)
const selectedDossierGraphPreviewTruncated = computed(
  () =>
    dossierGraphState.nodeCount > DOSSIER_GRAPH_PREVIEW_NODE_LIMIT ||
    dossierGraphState.edgeCount > DOSSIER_GRAPH_PREVIEW_EDGE_LIMIT
)
const selectedDossierGraphEmptyText = computed(() => {
  if (!selectedDossierGraphIsEmpty.value) return ''
  if (dossierGraphState.warning) {
    return dossierGraphState.warning
  }
  return '当前案卷还没有形成结构化关系。继续在持续跟踪会话中补充案情后，系统会自动整理。'
})
const conversationListEmptyText = computed(() =>
  conversationSearchKeyword.value.trim() ? '没有找到相关会话' : '暂无会话'
)
const localModelStatusText = computed(() => {
  if (localModel.loading) return '检查中...'
  if (!localModel.enabled) return '关闭（查询扩展走云端）'
  if (localModel.healthy) return `✅ 已连接 (${localModel.vllmUrl || '127.0.0.1:8001'})`
  return '❌ 连接失败'
})
const localModelStatusClass = computed(() => {
  if (!localModel.enabled) return 'is-off'
  return localModel.healthy ? 'is-on' : 'is-error'
})

const inputPlaceholder = computed(() => {
  if (!systemStatus.ready) return '系统初始化中，输入框暂时锁定。'
  if (!currentConversationId.value) return '请先创建或选择一个会话。'
  return '请输入法律问题，Enter 发送，Shift + Enter 换行。'
})

onMounted(async () => {
  startSystemStatusPolling()
  await refreshLocalModelStatus()
  await initializeConversations()
})

onUnmounted(() => {
  stopSystemStatusPolling()
  stopGraphRefreshPolling()
})

watch(dossierDrawerVisible, (visible) => {
  if (!visible) {
    dossierParticipantCandidateDialogVisible.value = false
    dossierAdvancedSections.value = []
    resetParticipantCandidateCreateDraft()
  }
})

function componentLabel(value) {
  return value === 'connected' ? '已连接' : '加载中'
}

async function refreshLocalModelStatus() {
  try {
    const { data } = await getLocalModelStatus()
    localModel.enabled = Boolean(data.enabled)
    localModel.healthy = Boolean(data.vllm_healthy)
    localModel.vllmUrl = data.vllm_url || '127.0.0.1:8001'
  } catch (error) {
    console.error('Failed to fetch local model status:', error)
    localModel.enabled = false
    localModel.healthy = false
    localModel.vllmUrl = '127.0.0.1:8001'
  }
}

async function handleLocalModelToggle(enabled) {
  localModel.loading = true
  try {
    const { data } = await setLocalModelStatus(Boolean(enabled))
    if (!data.success) {
      localModel.enabled = false
      localModel.healthy = false
      ElMessage.warning(data.message || '请先启动 vLLM 服务')
      return
    }
    await refreshLocalModelStatus()
    if (localModel.enabled && !localModel.healthy) {
      localModel.enabled = false
      ElMessage.warning('请先启动 vLLM 服务')
    }
  } catch (error) {
    console.error('Failed to toggle local model:', error)
    localModel.enabled = false
    localModel.healthy = false
    ElMessage.error('本地模型设置失败')
  } finally {
    localModel.loading = false
  }
}

function sortConversations(list) {
  return [...list].sort((left, right) => {
    const leftTime = new Date(left.updated_at || left.created_at || 0).getTime()
    const rightTime = new Date(right.updated_at || right.created_at || 0).getTime()
    return rightTime - leftTime
  })
}

function setConversations(list) {
  conversations.value = sortConversations(list)
}

function sortDossiers(list) {
  return [...list].sort((left, right) => {
    const leftTime = new Date(left.updated_at || left.created_at || 0).getTime()
    const rightTime = new Date(right.updated_at || right.created_at || 0).getTime()
    return rightTime - leftTime
  })
}

function setDossiers(list) {
  dossiers.value = sortDossiers(list)
}

function upsertDossierLocal(dossier) {
  if (!dossier) return
  const next = [...dossiers.value]
  const index = next.findIndex((item) => item.id === dossier.id)
  if (index >= 0) next[index] = { ...next[index], ...dossier }
  else next.push(dossier)
  setDossiers(next)
}

function upsertConversationLocal(conversation) {
  if (!conversation) return
  const next = [...conversations.value]
  const index = next.findIndex((item) => item.id === conversation.id)
  if (index >= 0) next[index] = { ...next[index], ...conversation }
  else next.push(conversation)
  setConversations(next)
}

function removeConversationLocal(conversationId) {
  setConversations(conversations.value.filter((item) => item.id !== conversationId))
}

function touchConversationLocal(conversationId, patch = {}) {
  const target = conversations.value.find((item) => item.id === conversationId)
  if (!target) return
  upsertConversationLocal({
    ...target,
    ...patch,
    updated_at: new Date().toISOString(),
  })
}

function resolveConversationRecord(conversationId) {
  if (!conversationId) return null
  if (currentConversation.value?.id === conversationId) {
    return currentConversation.value
  }
  return conversations.value.find((item) => item.id === conversationId) || null
}

function isTrackedDossierConversation(conversation) {
  if (!conversation?.dossier_id) return false
  return (
    String(conversation?.persistence_consent || '').trim() === 'tracked' ||
    String(conversation?.conversation_mode || '').trim() === 'TRACKED_CASE'
  )
}

function ensureDossierParticipantSection(sectionName) {
  if (!sectionName) return
  if (!dossierAdvancedSections.value.includes('advanced')) {
    dossierAdvancedSections.value = ['advanced']
  }
  const current = Array.isArray(dossierParticipantSections.value)
    ? [...dossierParticipantSections.value]
    : []
  if (!current.includes(sectionName)) {
    current.push(sectionName)
    dossierParticipantSections.value = current
  }
}

function toggleDossierParticipantSection(sectionName) {
  if (!sectionName) return
  const current = Array.isArray(dossierParticipantSections.value)
    ? [...dossierParticipantSections.value]
    : []
  if (current.includes(sectionName)) {
    dossierParticipantSections.value = current.filter((item) => item !== sectionName)
    return
  }
  current.push(sectionName)
  dossierParticipantSections.value = current
}

function openParticipantCandidateDialog() {
  if (!selectedDossierPendingParticipantCount.value) {
    if (selectedDossierRawLabelCandidates.value.length) {
      openParticipantAdvancedPanel('participants')
      ElMessage.info('当前没有待确认的跨会话主体关系，其他 raw-label 线索已放入高级/调试详情')
      return
    }
    ElMessage.info('当前没有待确认的主体线索')
    return
  }
  dossierParticipantCandidateDialogVisible.value = true
}

function resetDossierForm() {
  dossierForm.title = ''
  dossierForm.case_type = ''
  dossierForm.summary = ''
}

function defaultParticipantDrafts(caseType = '') {
  const normalized = String(caseType || '').trim()
  const isLabor = normalized.includes('劳动')
  return {
    title: isLabor ? '劳动争议案卷' : '案情跟踪案卷',
    primary_label: '我',
    primary_role: isLabor ? '劳动者' : '当事人',
    opposing_label: isLabor ? '公司' : '对方',
    opposing_role: isLabor ? '用人单位' : '对方当事人',
  }
}

function applyParticipantAliasesToForm(form, participantAliases = {}, caseType = '') {
  const defaults = defaultParticipantDrafts(caseType)
  const primary = participantAliases?.primary_party || {}
  const opposing = participantAliases?.opposing_party || {}
  form.primary_label = primary.label || defaults.primary_label
  form.primary_role = primary.role || defaults.primary_role
  form.opposing_label = opposing.label || defaults.opposing_label
  form.opposing_role = opposing.role || defaults.opposing_role
}

function resetDossierParticipantForm(caseType = '') {
  applyParticipantAliasesToForm(dossierParticipantForm, {}, caseType)
}

function resetDossierParticipantCreateForm() {
  dossierParticipantCreateForm.label = ''
  dossierParticipantCreateForm.role = ''
  dossierParticipantCreateForm.party_side = 'unknown'
  dossierParticipantCreateForm.entity_type = 'unknown'
  dossierParticipantCreateForm.description = ''
}

function resetDossierDetailForm() {
  dossierDetailForm.title = ''
  dossierDetailForm.case_type = ''
  dossierDetailForm.summary = ''
  dossierDetailForm.status = 'active'
  resetDossierParticipantForm()
  resetDossierParticipantCreateForm()
}

function resetTrackedOnboardingForm(caseType = '') {
  const defaults = defaultParticipantDrafts(caseType)
  trackedOnboardingForm.title = defaults.title
  trackedOnboardingForm.primary_label = defaults.primary_label
  trackedOnboardingForm.primary_role = defaults.primary_role
  trackedOnboardingForm.opposing_label = defaults.opposing_label
  trackedOnboardingForm.opposing_role = defaults.opposing_role
}

function clearTrackedOnboardingState() {
  trackedOnboardingState.conversationId = null
  trackedOnboardingState.text = ''
  trackedOnboardingState.forceCurrentCase = false
  trackedOnboardingState.draftText = ''
  trackedOnboardingState.dossierId = null
  resetTrackedOnboardingForm()
}

function applySelectedDossierDetail(dossier) {
  selectedDossierDetail.value = dossier || null
  if (!dossier) {
    resetDossierDetailForm()
    resetDossierParticipantRegistryState()
    resetDossierIntelligenceState()
    resetDossierCoordinationGraphState()
    return
  }

  dossierDetailForm.title = dossier.title || ''
  dossierDetailForm.case_type = dossier.case_type || ''
  dossierDetailForm.summary = dossier.summary || ''
  dossierDetailForm.status = dossier.status || 'active'
  applyParticipantAliasesToForm(
    dossierParticipantForm,
    dossier.participant_aliases || {},
    dossier.case_type || ''
  )
  applyDossierParticipantRegistryPayload({
    participants: dossier.confirmed_participants || [],
    mentions: dossier.participant_mentions || [],
  })
}

function resetDossierParticipantRegistryState() {
  dossierParticipantRegistryState.participants = []
  dossierParticipantRegistryState.mentions = []
  dossierParticipantRegistryState.candidates = []
  dossierParticipantRegistryState.error = ''
  dossierAdvancedSections.value = []
  dossierParticipantSections.value = []
  dossierParticipantCandidateDialogVisible.value = false
  resetDossierParticipantCreateForm()
  resetParticipantCandidateCreateDraft()
  Object.keys(dossierParticipantCandidateSelections).forEach((key) => {
    delete dossierParticipantCandidateSelections[key]
  })
  Object.keys(dossierParticipantCandidateDismissed).forEach((key) => {
    delete dossierParticipantCandidateDismissed[key]
  })
}

function applyDossierParticipantRegistryPayload(payload) {
  dossierParticipantRegistryState.participants = Array.isArray(payload?.participants)
    ? payload.participants
    : []
  dossierParticipantRegistryState.mentions = Array.isArray(payload?.mentions)
    ? payload.mentions
    : []
  dossierParticipantRegistryState.error = ''
}

function applyDossierParticipantCandidatesPayload(payload) {
  dossierParticipantRegistryState.candidates = Array.isArray(payload?.candidates)
    ? payload.candidates
    : []
  dossierParticipantRegistryState.error = ''
  resetParticipantCandidateCreateDraft()
  Object.keys(dossierParticipantCandidateSelections).forEach((key) => {
    delete dossierParticipantCandidateSelections[key]
  })
  for (const candidate of dossierParticipantRegistryState.candidates) {
    const key = candidateKey(candidate)
    if (!key) continue
    dossierParticipantCandidateSelections[key] = null
  }
}

function resetDossierIntelligenceState() {
  dossierIntelligenceState.dossierId = null
  dossierIntelligenceState.summary = {
    text: '',
    source: '',
    updatedAt: '',
  }
  dossierIntelligenceState.confirmationTasks = []
  dossierIntelligenceState.displayGraphNotes = []
  dossierIntelligenceState.provider = ''
  dossierIntelligenceState.model = ''
  dossierIntelligenceState.fallbackReason = ''
  dossierIntelligenceState.traceSteps = []
  dossierIntelligenceState.traceWarnings = []
  dossierIntelligenceState.error = ''
  dossierPartyNamingSubmittingKey.value = ''
  clearPartyNamingDrafts()
  clearSelfIdentityTaskStates()
}

function applyDossierIntelligencePayload(dossierId, payload) {
  const summary = payload?.summary && typeof payload.summary === 'object' ? payload.summary : {}
  const trace = payload?.trace && typeof payload.trace === 'object' ? payload.trace : {}

  dossierIntelligenceState.dossierId = dossierId
  dossierIntelligenceState.summary = {
    text: typeof summary.text === 'string' ? summary.text : '',
    source: typeof summary.source === 'string' ? summary.source : '',
    updatedAt: typeof summary.updated_at === 'string' ? summary.updated_at : '',
  }
  dossierIntelligenceState.confirmationTasks = Array.isArray(payload?.confirmation_tasks)
    ? payload.confirmation_tasks
    : []
  dossierIntelligenceState.displayGraphNotes = Array.isArray(payload?.display_graph_notes)
    ? payload.display_graph_notes
    : []
  dossierIntelligenceState.provider =
    typeof payload?.provider === 'string' ? payload.provider : ''
  dossierIntelligenceState.model = typeof payload?.model === 'string' ? payload.model : ''
  dossierIntelligenceState.fallbackReason =
    typeof payload?.fallback_reason === 'string' ? payload.fallback_reason : ''
  dossierIntelligenceState.traceSteps = Array.isArray(trace.steps) ? trace.steps : []
  dossierIntelligenceState.traceWarnings = Array.isArray(trace.warnings) ? trace.warnings : []
  dossierIntelligenceState.error = ''
  syncPartyNamingDrafts(dossierIntelligenceState.confirmationTasks)
  syncSelfIdentityTaskStates(dossierIntelligenceState.confirmationTasks)
}

function dossierIntelligenceTaskKey(task) {
  if (
    String(task?.task_type || '').trim() === 'entity_identity' &&
    String(task?.subject || '').trim() === 'self_anchor'
  ) {
    return dossierSelfIdentityTaskKey(task)
  }

  const evidence = task?.evidence && typeof task.evidence === 'object' ? task.evidence : {}
  const rawNodeIds = Array.isArray(evidence.raw_node_ids)
    ? evidence.raw_node_ids.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  const conversationIds = Array.isArray(evidence.source_conversation_ids)
    ? evidence.source_conversation_ids
      .map((item) => Number(item || 0))
      .filter((item) => Number.isInteger(item) && item > 0)
    : []
  const rawLabels = Array.isArray(evidence.raw_labels)
    ? evidence.raw_labels.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  const base = [
    String(task?.task_type || '').trim(),
    String(task?.subject || '').trim(),
    String(task?.current_label || '').trim(),
    rawNodeIds.join('|'),
    conversationIds.join('|'),
    rawLabels.join('|'),
  ]
    .filter(Boolean)
    .join(':')

  return base || 'intelligence-task:party-naming'
}

function dossierSelfIdentityTaskKey(task) {
  const sourceNode = task?.source_node && typeof task.source_node === 'object' ? task.source_node : {}
  const targetParticipant =
    task?.target_participant && typeof task.target_participant === 'object'
      ? task.target_participant
      : {}
  const base = [
    'entity_identity',
    'self_anchor',
    String(sourceNode.graph_node_id || '').trim(),
    Number(sourceNode.conversation_id || 0) > 0 ? String(Number(sourceNode.conversation_id)) : '',
    String(sourceNode.raw_label || '').trim(),
    Number(targetParticipant.participant_id || 0) > 0
      ? String(Number(targetParticipant.participant_id))
      : '',
  ]
    .filter(Boolean)
    .join(':')

  return base || 'intelligence-task:self-identity'
}

function defaultPartyNamingDraft(task) {
  const suggestedLabels = Array.isArray(task?.suggested_labels)
    ? task.suggested_labels.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  if (suggestedLabels.length) {
    return suggestedLabels[0]
  }
  return String(task?.current_label || '').trim()
}

function clearPartyNamingDrafts() {
  for (const key of Object.keys(dossierPartyNamingDrafts)) {
    delete dossierPartyNamingDrafts[key]
  }
}

function clearSelfIdentityTaskStates() {
  for (const key of Object.keys(dossierSelfIdentityTaskStates)) {
    delete dossierSelfIdentityTaskStates[key]
  }
}

function syncPartyNamingDrafts(tasks) {
  const nextKeys = new Set()
  for (const task of Array.isArray(tasks) ? tasks : []) {
    if (task?.task_type !== 'party_naming') continue
    const key = dossierIntelligenceTaskKey(task)
    nextKeys.add(key)
    if (!String(dossierPartyNamingDrafts[key] || '').trim()) {
      dossierPartyNamingDrafts[key] = defaultPartyNamingDraft(task)
    }
  }

  for (const key of Object.keys(dossierPartyNamingDrafts)) {
    if (!nextKeys.has(key)) {
      delete dossierPartyNamingDrafts[key]
    }
  }
}

function ensureSelfIdentityTaskState(task) {
  const key = dossierSelfIdentityTaskKey(task)
  if (!dossierSelfIdentityTaskStates[key]) {
    dossierSelfIdentityTaskStates[key] = {
      differentMode: false,
      differentDraft: '',
      submittingAction: '',
      error: '',
      dismissed: false,
    }
  }
  return dossierSelfIdentityTaskStates[key]
}

function syncSelfIdentityTaskStates(tasks) {
  const nextKeys = new Set()
  for (const task of Array.isArray(tasks) ? tasks : []) {
    if (
      String(task?.task_type || '').trim() !== 'entity_identity' ||
      String(task?.subject || '').trim() !== 'self_anchor'
    ) {
      continue
    }
    const key = dossierSelfIdentityTaskKey(task)
    nextKeys.add(key)
    ensureSelfIdentityTaskState(task)
  }

  for (const key of Object.keys(dossierSelfIdentityTaskStates)) {
    if (!nextKeys.has(key)) {
      delete dossierSelfIdentityTaskStates[key]
    }
  }
}

function selfIdentityTaskState(task) {
  return ensureSelfIdentityTaskState(task)
}

function selfIdentityTaskIsDismissed(task) {
  return Boolean(selfIdentityTaskState(task).dismissed)
}

function selfIdentityTaskIsDifferentMode(task) {
  return Boolean(selfIdentityTaskState(task).differentMode)
}

function selfIdentityTaskError(task) {
  return String(selfIdentityTaskState(task).error || '').trim()
}

function selfIdentityTaskIsSubmitting(task) {
  return Boolean(String(selfIdentityTaskState(task).submittingAction || '').trim())
}

function selfIdentityTaskIsSubmittingAction(task, action) {
  return String(selfIdentityTaskState(task).submittingAction || '').trim() === String(action || '').trim()
}

function isPartyNamingSubmitting(task) {
  return dossierPartyNamingSubmittingKey.value === dossierIntelligenceTaskKey(task)
}

function canSubmitPartyNaming(task) {
  return Boolean(String(dossierPartyNamingDrafts[dossierIntelligenceTaskKey(task)] || '').trim())
}

function buildPartyNamingConfirmPayload(task, displayLabel) {
  const evidence = task?.evidence && typeof task.evidence === 'object' ? task.evidence : {}
  return {
    display_label: String(displayLabel || '').trim(),
    source_task: {
      subject: String(task?.subject || 'self_anchor').trim() || 'self_anchor',
      evidence: {
        raw_labels: Array.isArray(evidence.raw_labels)
          ? evidence.raw_labels.map((item) => String(item || '').trim()).filter(Boolean)
          : [],
        source_conversation_ids: Array.isArray(evidence.source_conversation_ids)
          ? evidence.source_conversation_ids
            .map((item) => Number(item || 0))
            .filter((item) => Number.isInteger(item) && item > 0)
          : [],
        raw_node_ids: Array.isArray(evidence.raw_node_ids)
          ? evidence.raw_node_ids.map((item) => String(item || '').trim()).filter(Boolean)
          : [],
      },
    },
  }
}

function resetDossierCoordinationGraphState() {
  dossierCoordinationGraphState.dossierId = null
  dossierCoordinationGraphState.entities = []
  dossierCoordinationGraphState.edges = []
  dossierCoordinationGraphState.tasks = []
  dossierCoordinationGraphState.stats = {
    boundConversations: 0,
    connectedEntities: 0,
    pendingIdentity: 0,
    pendingNaming: 0,
    isolatedAnchors: 0,
  }
  dossierCoordinationGraphState.view = 'coordination'
  dossierCoordinationGraphState.generatedAt = ''
  dossierCoordinationGraphState.error = ''
}

function applyDossierCoordinationGraphPayload(dossierId, payload) {
  const coordinationGraph = payload?.coordination_graph || {}
  const stats = coordinationGraph?.stats || {}

  dossierCoordinationGraphState.dossierId = dossierId
  dossierCoordinationGraphState.entities = Array.isArray(coordinationGraph.entities)
    ? coordinationGraph.entities
    : []
  dossierCoordinationGraphState.edges = Array.isArray(coordinationGraph.edges)
    ? coordinationGraph.edges
    : []
  dossierCoordinationGraphState.tasks = Array.isArray(coordinationGraph.tasks)
    ? coordinationGraph.tasks
    : []
  dossierCoordinationGraphState.stats = {
    boundConversations: Number(stats.bound_conversations || 0),
    connectedEntities: Number(stats.connected_entities || 0),
    pendingIdentity: Number(stats.pending_identity || 0),
    pendingNaming: Number(stats.pending_naming || 0),
    isolatedAnchors: Number(stats.isolated_anchors || 0),
  }
  dossierCoordinationGraphState.view = String(payload?.view || 'coordination').trim() || 'coordination'
  dossierCoordinationGraphState.generatedAt = payload?.generated_at || ''
  dossierCoordinationGraphState.error = ''
}

function resetDossierGraphState() {
  dossierGraphState.dossierId = null
  dossierGraphState.conversationCount = 0
  dossierGraphState.nodeCount = 0
  dossierGraphState.edgeCount = 0
  dossierGraphState.nodes = []
  dossierGraphState.edges = []
  dossierGraphState.view = 'raw'
  dossierGraphState.generatedAt = ''
  dossierGraphState.warning = ''
  dossierGraphState.error = ''
  dossierGraphState.participantAliases = {}
  dossierGraphLoaded.value = false
}

function deriveDossierGraphConversationCount(nodes, edges) {
  const conversationIds = [
    ...nodes.flatMap((node) => graphNodeConversationIds(node)),
    ...edges.flatMap((edge) => normalizeGraphConversationIds(edge?.conversation_ids)),
    ...edges.flatMap((edge) => normalizeGraphConversationIds(edge?.source_conversation_ids)),
    ...edges.flatMap((edge) => normalizeGraphConversationIds(edge?.target_conversation_ids)),
  ].filter((value, index, array) => array.indexOf(value) === index)

  if (conversationIds.length) {
    return conversationIds.length
  }
  return Number(selectedDossierConversationCount.value || 0)
}

function applyDossierGraphPayload(dossierId, payload, options = {}) {
  const stats = payload?.graph?.stats || {}
  const graph = payload?.graph || {}
  const participantAliases = payload?.participant_aliases
  dossierGraphState.dossierId = dossierId
  dossierGraphState.conversationCount =
    Number(stats.conversation_count || 0) ||
    deriveDossierGraphConversationCount(
      Array.isArray(graph.nodes) ? graph.nodes : [],
      Array.isArray(graph.edges) ? graph.edges : []
    )
  dossierGraphState.nodeCount = Number(stats.node_count || 0)
  dossierGraphState.edgeCount = Number(stats.edge_count || 0)
  dossierGraphState.nodes = Array.isArray(graph.nodes) ? graph.nodes : []
  dossierGraphState.edges = Array.isArray(graph.edges) ? graph.edges : []
  dossierGraphState.view = 'raw'
  dossierGraphState.generatedAt = payload?.generated_at || ''
  dossierGraphState.warning =
    typeof options?.warning === 'string' && options.warning
      ? options.warning
      : typeof payload?.warning === 'string'
        ? payload.warning
        : ''
  dossierGraphState.error = ''
  dossierGraphState.participantAliases =
    participantAliases && typeof participantAliases === 'object'
      ? participantAliases
      : selectedDossierDetail.value?.participant_aliases || {}
  dossierGraphLoaded.value = true
}

function applyDossierDisplayGraphPayload(dossierId, payload) {
  const displayGraph = payload?.display_graph || {}
  const stats = displayGraph?.stats || {}
  const nodes = Array.isArray(displayGraph.nodes) ? displayGraph.nodes : []
  const edges = Array.isArray(displayGraph.edges) ? displayGraph.edges : []

  dossierGraphState.dossierId = dossierId
  dossierGraphState.conversationCount = deriveDossierGraphConversationCount(nodes, edges)
  dossierGraphState.nodeCount = Number(stats.display_node_count || nodes.length || 0)
  dossierGraphState.edgeCount = Number(stats.display_edge_count || edges.length || 0)
  dossierGraphState.nodes = nodes
  dossierGraphState.edges = edges
  dossierGraphState.view = String(payload?.view || 'display').trim() || 'display'
  dossierGraphState.generatedAt = payload?.generated_at || ''
  dossierGraphState.warning = typeof payload?.warning === 'string' ? payload.warning : ''
  dossierGraphState.error = ''
  dossierGraphState.participantAliases = selectedDossierDetail.value?.participant_aliases || {}
  dossierGraphLoaded.value = true
}

function resetAnalysis() {
  currentAnalysis.actions = []
  currentAnalysis.sources = []
  currentAnalysis.routeIntent = ''
  currentAnalysis.latencyMs = null
  currentAnalysis.confidenceScore = null
  currentAnalysis.missingInfo = []
  currentAnalysis.requestState = 'idle'
  currentAnalysis.requestError = ''
  currentAnalysis.updatedAt = ''
  currentAnalysis.graphSyncStatus = 'idle'
  currentAnalysis.graphSyncSummary = ''
  currentAnalysis.graphSyncUpdatedAt = ''
}

function resetGraph() {
  currentGraph.query = ''
  currentGraph.nodes = []
  currentGraph.edges = []
  currentGraph.tokens = []
  currentGraph.generatedAt = ''
  currentGraph.loading = false
  currentGraph.error = ''
}

function clearCurrentConversationState() {
  currentConversationId.value = null
  currentSessionId.value = ''
  messages.value = []
  resetAnalysis()
  resetGraph()
}

function setGraphSyncState(status = 'idle', summary = '') {
  currentAnalysis.graphSyncStatus = String(status || 'idle').trim() || 'idle'
  currentAnalysis.graphSyncSummary = String(summary || '').trim()
  currentAnalysis.graphSyncUpdatedAt = new Date().toISOString()
}

function applyCurrentAnalysisResponse(data = {}) {
  currentAnalysis.actions = Array.isArray(data.agent_actions) ? data.agent_actions : []
  currentAnalysis.sources = Array.isArray(data.sources) ? data.sources : []
  currentAnalysis.routeIntent = String(data.route_intent || '').trim()
  currentAnalysis.latencyMs = Number.isFinite(Number(data.latency_ms))
    ? Number(data.latency_ms)
    : null
  currentAnalysis.confidenceScore = Number.isFinite(Number(data.confidence_score))
    ? Number(data.confidence_score)
    : null
  currentAnalysis.missingInfo = Array.isArray(data.missing_info) ? data.missing_info : []
  currentAnalysis.requestState = 'success'
  currentAnalysis.requestError = ''
  currentAnalysis.updatedAt = new Date().toISOString()
}

function stopGraphRefreshPolling(options = {}) {
  if (graphRefreshTimer.value) {
    clearInterval(graphRefreshTimer.value)
    graphRefreshTimer.value = null
  }
  if (graphRefreshStopTimer.value) {
    clearTimeout(graphRefreshStopTimer.value)
    graphRefreshStopTimer.value = null
  }
  if (options?.status) {
    setGraphSyncState(options.status, options.summary || '')
  }
}

function buildMessageViewModel(message, index = 0) {
  const metadata = message.metadata || {}
  return {
    id: message.id || `${message.role}-${index}`,
    key: `${message.id || 'message'}-${index}`,
    role: message.role === 'assistant' ? 'assistant' : 'user',
    content: message.content || '',
    time: formatTime(message.created_at),
    createdAt: message.created_at || '',
    sources: metadata.sources || [],
    agentActions: metadata.agent_actions || [],
    routeIntent: metadata.route_intent || '',
    latencyMs: metadata.latency_ms ?? null,
    confidenceScore: metadata.confidence_score ?? null,
    missingInfo: metadata.missing_info || [],
    pending: false,
  }
}

function syncCurrentAnalysisFromMessages() {
  resetAnalysis()
  const latestAssistant = [...messages.value].reverse().find((item) => item.role !== 'user')
  if (!latestAssistant) return
  currentAnalysis.actions = latestAssistant.agentActions || []
  currentAnalysis.sources = latestAssistant.sources || []
  currentAnalysis.routeIntent = String(latestAssistant.routeIntent || '').trim()
  currentAnalysis.latencyMs = Number.isFinite(Number(latestAssistant.latencyMs))
    ? Number(latestAssistant.latencyMs)
    : null
  currentAnalysis.confidenceScore = Number.isFinite(Number(latestAssistant.confidenceScore))
    ? Number(latestAssistant.confidenceScore)
    : null
  currentAnalysis.missingInfo = Array.isArray(latestAssistant.missingInfo)
    ? latestAssistant.missingInfo
    : []
  currentAnalysis.requestState = 'success'
  currentAnalysis.updatedAt = latestAssistant.createdAt || ''
  if (currentGraph.loading) {
    setGraphSyncState('running', '图谱同步任务仍在刷新中')
  }
}

function applyConversationPayload(payload) {
  const conversation = payload?.conversation
  const backendMessages = Array.isArray(payload?.messages) ? payload.messages : []

  if (conversation) {
    upsertConversationLocal(conversation)
    currentConversationId.value = conversation.id
    currentSessionId.value = conversation.session_id || ''
  }

  messages.value = backendMessages.map((message, index) => buildMessageViewModel(message, index))
  syncCurrentAnalysisFromMessages()
  scrollBottom()
}

async function initializeConversations() {
  try {
    await refreshConversationList()
    if (!conversations.value.length) {
      await createNewConversation({ silent: true })
      return
    }

    await switchConversation(conversations.value[0].id)
  } catch (error) {
    console.error('Failed to initialize conversations:', error)
    ElMessage.error('加载会话列表失败。')
  }
}

async function refreshConversationList(preferredConversationId = null) {
  conversationListLoading.value = true
  try {
    const keyword = conversationSearchKeyword.value.trim()
    const { data } = keyword
      ? await searchConversations(keyword)
      : await getConversations()
    const nextList = Array.isArray(data.conversations)
      ? data.conversations
      : Array.isArray(data.cases)
        ? data.cases
        : []
    setConversations(nextList)

    const activeId = preferredConversationId || currentConversationId.value
    const active = conversations.value.find((item) => item.id === activeId)
    if (active) {
      currentConversationId.value = active.id
      currentSessionId.value = active.session_id || ''
    }
  } finally {
    conversationListLoading.value = false
  }
}

async function refreshDossiers(options = {}) {
  dossierListLoading.value = true
  try {
    const { data } = await getDossiers()
    const nextDossiers = Array.isArray(data.dossiers) ? data.dossiers : []
    setDossiers(nextDossiers)

    if (!nextDossiers.length) {
      selectedDossierId.value = null
      applySelectedDossierDetail(null)
      resetDossierGraphState()
      return
    }

    const preferredId =
      options.preferredDossierId ??
      selectedDossierId.value ??
      currentConversation.value?.dossier_id ??
      nextDossiers[0]?.id

    selectedDossierId.value =
      nextDossiers.find((item) => item.id === preferredId)?.id || nextDossiers[0]?.id || null
  } catch (error) {
    console.error('Failed to fetch dossiers:', error)
    if (!options.silent) {
      ElMessage.error('加载案卷列表失败')
    }
  } finally {
    dossierListLoading.value = false
  }
}

async function loadSelectedDossierDetail(dossierId, options = {}) {
  if (!dossierId) {
    selectedDossierId.value = null
    applySelectedDossierDetail(null)
    resetDossierGraphState()
    resetDossierCoordinationGraphState()
    resetDossierIntelligenceState()
    return
  }

  selectedDossierId.value = dossierId
  dossierDetailLoading.value = true
  let detailLoaded = false
  try {
    const { data } = await getDossierDetail(dossierId)
    const dossier = data?.dossier || null
    applySelectedDossierDetail(dossier)
    if (dossier) {
      upsertDossierLocal(dossier)
      detailLoaded = true
    }
  } catch (error) {
    console.error('Failed to fetch dossier detail:', error)
    applySelectedDossierDetail(null)
    resetDossierGraphState()
    resetDossierCoordinationGraphState()
    resetDossierIntelligenceState()
    if (!options.silent) {
      ElMessage.error('Failed to load dossier detail')
    }
  } finally {
    dossierDetailLoading.value = false
  }

  if (detailLoaded && options.loadParticipants !== false) {
    await loadSelectedDossierParticipants(dossierId, { silent: true })
    await loadSelectedDossierParticipantCandidates(dossierId, { silent: true })
  }
  if (detailLoaded && options.loadGraph !== false) {
    await loadSelectedDossierGraph(dossierId, { silent: true })
  }
  if (detailLoaded && options.loadCoordinationGraph !== false) {
    await loadSelectedDossierCoordinationGraph(dossierId, { silent: true })
  }
  if (detailLoaded && options.loadIntelligence !== false) {
    await loadSelectedDossierIntelligence(dossierId)
  }
}

async function loadSelectedDossierParticipants(dossierId, options = {}) {
  if (!dossierId) {
    resetDossierParticipantRegistryState()
    return
  }

  dossierParticipantRegistryLoading.value = true
  dossierParticipantRegistryState.error = ''
  try {
    const { data } = await getDossierParticipants(dossierId)
    applyDossierParticipantRegistryPayload(data)
  } catch (error) {
    console.error('Failed to fetch dossier participants:', error)
    dossierParticipantRegistryState.error =
      typeof error?.response?.data?.detail === 'string' && error.response.data.detail
        ? error.response.data.detail
        : '主体列表加载失败，请稍后重试'
    if (!options.silent) {
      ElMessage.error(dossierParticipantRegistryState.error)
    }
  } finally {
    dossierParticipantRegistryLoading.value = false
  }
}

async function loadSelectedDossierParticipantCandidates(dossierId, options = {}) {
  if (!dossierId) {
    dossierParticipantRegistryState.candidates = []
    return
  }

  dossierParticipantCandidatesLoading.value = true
  dossierParticipantRegistryState.error = ''
  try {
    const { data } = await getDossierParticipantCandidates(dossierId)
    applyDossierParticipantCandidatesPayload(data)
  } catch (error) {
    console.error('Failed to fetch dossier participant candidates:', error)
    dossierParticipantRegistryState.error =
      typeof error?.response?.data?.detail === 'string' && error.response.data.detail
        ? error.response.data.detail
        : '主体候选加载失败，请稍后重试'
    if (!options.silent) {
      ElMessage.error(dossierParticipantRegistryState.error)
    }
  } finally {
    dossierParticipantCandidatesLoading.value = false
  }
}

async function loadSelectedDossierGraph(dossierId, options = {}) {
  if (!dossierId) {
    resetDossierGraphState()
    return
  }

  const isSameDossier = dossierGraphState.dossierId === dossierId
  if (!isSameDossier) {
    resetDossierGraphState()
    dossierGraphState.dossierId = dossierId
  }

  dossierGraphLoading.value = true
  dossierGraphState.error = ''
  dossierGraphState.warning = ''
  try {
    try {
      const { data } = await getDossierDisplayGraph(dossierId)
      applyDossierDisplayGraphPayload(dossierId, data)
      return
    } catch (displayGraphError) {
      console.warn('Failed to fetch dossier display graph, falling back to raw graph:', displayGraphError)
    }

    const { data } = await getDossierGraph(dossierId)
    applyDossierGraphPayload(dossierId, data, {
      warning: '展示图谱暂不可用，当前回退为原始案卷图谱。',
    })
  } catch (error) {
    console.error('Failed to fetch dossier graph:', error)
    dossierGraphState.dossierId = dossierId
    dossierGraphState.warning = ''
    dossierGraphState.error =
      typeof error?.response?.data?.detail === 'string' && error.response.data.detail
        ? error.response.data.detail
        : '图谱状态加载失败，请稍后重试'
    dossierGraphLoaded.value = true
    if (!options.silent) {
      ElMessage.error(dossierGraphState.error)
    }
  } finally {
    dossierGraphLoading.value = false
  }
}

async function loadSelectedDossierCoordinationGraph(dossierId, options = {}) {
  if (!dossierId) {
    resetDossierCoordinationGraphState()
    return
  }

  const isSameDossier = dossierCoordinationGraphState.dossierId === dossierId
  if (!isSameDossier) {
    resetDossierCoordinationGraphState()
    dossierCoordinationGraphState.dossierId = dossierId
  }

  dossierCoordinationGraphLoading.value = true
  dossierCoordinationGraphState.error = ''
  try {
    const { data } = await getDossierCoordinationGraph(dossierId)
    applyDossierCoordinationGraphPayload(dossierId, data)
  } catch (error) {
    console.error('Failed to fetch dossier coordination graph:', error)
    dossierCoordinationGraphState.dossierId = dossierId
    dossierCoordinationGraphState.error =
      typeof error?.response?.data?.detail === 'string' && error.response.data.detail
        ? error.response.data.detail
        : '案卷实体连接暂不可用'
    if (!options.silent) {
      ElMessage.error(dossierCoordinationGraphState.error)
    }
  } finally {
    dossierCoordinationGraphLoading.value = false
  }
}

async function loadSelectedDossierIntelligence(dossierId) {
  if (!dossierId) {
    resetDossierIntelligenceState()
    return
  }

  dossierIntelligenceLoading.value = true
  dossierIntelligenceState.error = ''
  try {
    const { data } = await getDossierIntelligence(dossierId, {
      include_display_graph: true,
    })
    applyDossierIntelligencePayload(dossierId, data)
  } catch (error) {
    console.error('Failed to fetch dossier intelligence:', error)
    dossierIntelligenceState.dossierId = dossierId
    dossierIntelligenceState.error = '案卷智能概览暂不可用'
  } finally {
    dossierIntelligenceLoading.value = false
  }
}

async function handleRefreshDossierGraph() {
  if (!selectedDossierId.value) return
  await loadSelectedDossierGraph(selectedDossierId.value)
}

async function handleRefreshDossierCoordination() {
  if (!selectedDossierId.value) return
  await Promise.allSettled([
    loadSelectedDossierCoordinationGraph(selectedDossierId.value),
    loadSelectedDossierIntelligence(selectedDossierId.value),
  ])
}

async function handleRefreshDossierIntelligence() {
  if (!selectedDossierId.value) return
  await loadSelectedDossierIntelligence(selectedDossierId.value)
}

async function refreshSelectedDossierAfterPartyNaming(dossierId) {
  await Promise.allSettled([
    loadSelectedDossierCoordinationGraph(dossierId, { silent: true }),
    loadSelectedDossierIntelligence(dossierId),
    loadSelectedDossierParticipants(dossierId, { silent: true }),
    loadSelectedDossierParticipantCandidates(dossierId, { silent: true }),
    loadSelectedDossierGraph(dossierId, { silent: true }),
  ])

  const refreshErrors = [
    dossierCoordinationGraphState.error,
    dossierIntelligenceState.error,
    dossierParticipantRegistryState.error,
    dossierGraphState.error,
  ].filter((value) => String(value || '').trim())

  if (refreshErrors.length) {
    console.error('Failed to refresh dossier views after party naming confirmation:', refreshErrors)
  }
  return refreshErrors.length === 0
}

async function refreshSelectedDossierAfterSelfIdentity(dossierId) {
  await Promise.allSettled([
    loadSelectedDossierCoordinationGraph(dossierId, { silent: true }),
    loadSelectedDossierIntelligence(dossierId),
    loadSelectedDossierParticipants(dossierId, { silent: true }),
    loadSelectedDossierParticipantCandidates(dossierId, { silent: true }),
    loadSelectedDossierGraph(dossierId, { silent: true }),
  ])

  const refreshErrors = [
    dossierCoordinationGraphState.error,
    dossierIntelligenceState.error,
    dossierParticipantRegistryState.error,
    dossierGraphState.error,
  ].filter((value) => String(value || '').trim())

  if (refreshErrors.length) {
    console.error('Failed to refresh dossier views after self identity confirmation:', refreshErrors)
  }
  return refreshErrors.length === 0
}

function buildSelfIdentityConfirmSamePayload(task) {
  const sourceNode = task?.source_node && typeof task.source_node === 'object' ? task.source_node : {}
  const targetParticipant =
    task?.target_participant && typeof task.target_participant === 'object'
      ? task.target_participant
      : {}
  return {
    action: 'confirm_same',
    source_node: {
      conversation_id: Number(sourceNode.conversation_id || 0),
      raw_label: String(sourceNode.raw_label || '').trim(),
      graph_node_id: String(sourceNode.graph_node_id || '').trim(),
      graph_node_label: String(
        sourceNode.graph_node_label || sourceNode.raw_label || ''
      ).trim(),
      graph_node_type: String(sourceNode.graph_node_type || 'person').trim() || 'person',
    },
    target_participant_id: Number(targetParticipant.participant_id || 0),
  }
}

function buildSelfIdentityConfirmDifferentPayload(task, newDisplayLabel) {
  return {
    ...buildSelfIdentityConfirmSamePayload(task),
    action: 'confirm_different',
    new_display_label: String(newDisplayLabel || '').trim(),
  }
}

function selfIdentityTaskErrorMessage(error, fallback = '确认失败') {
  return (
    error?.response?.data?.detail ||
    error?.message ||
    fallback
  )
}

async function handleConfirmPartyNaming(task) {
  const dossierId = Number(selectedDossierId.value || 0)
  if (dossierId <= 0) {
    ElMessage.error('当前案卷不可用')
    return
  }

  const taskKey = dossierIntelligenceTaskKey(task)
  const displayLabel = String(dossierPartyNamingDrafts[taskKey] || '').trim()
  if (!displayLabel) {
    ElMessage.warning('请输入该主体在本案中的名称或代称')
    return
  }

  dossierPartyNamingSubmittingKey.value = taskKey
  try {
    await confirmDossierPartyNaming(
      dossierId,
      buildPartyNamingConfirmPayload(task, displayLabel),
    )

    const refreshed = await refreshSelectedDossierAfterPartyNaming(dossierId)
    if (refreshed) {
      ElMessage.success('已确认主体名称')
    } else {
      ElMessage.warning('已确认主体名称，部分视图刷新失败，请手动刷新')
    }
  } catch (error) {
    console.error('Failed to confirm dossier party naming:', error)
    const detail =
      typeof error?.response?.data?.detail === 'string' && error.response.data.detail
        ? error.response.data.detail
        : '确认主体名称失败'
    ElMessage.error(detail)
  } finally {
    dossierPartyNamingSubmittingKey.value = ''
  }
}

function handleDismissSelfIdentityTask(task) {
  const state = ensureSelfIdentityTaskState(task)
  state.dismissed = true
  state.differentMode = false
  state.error = ''
}

function handleOpenSelfIdentityDifferent(task) {
  const state = ensureSelfIdentityTaskState(task)
  state.dismissed = false
  state.differentMode = true
  state.error = ''
}

async function handleConfirmSelfIdentitySame(task) {
  const dossierId = Number(selectedDossierId.value || 0)
  if (dossierId <= 0) {
    return
  }

  const state = ensureSelfIdentityTaskState(task)
  state.error = ''
  state.dismissed = false
  state.submittingAction = 'confirm_same'

  try {
    await confirmDossierSelfIdentity(
      dossierId,
      buildSelfIdentityConfirmSamePayload(task),
    )

    const refreshed = await refreshSelectedDossierAfterSelfIdentity(dossierId)
    if (refreshed) {
      ElMessage.success('已确认是同一当事人')
    } else {
      ElMessage.warning('已确认是同一当事人，但部分视图刷新失败，请手动刷新')
    }
  } catch (error) {
    console.error('Failed to confirm dossier self identity as same participant:', error)
    state.error = selfIdentityTaskErrorMessage(error, '确认失败')
  } finally {
    state.submittingAction = ''
  }
}

async function handleConfirmSelfIdentityDifferent(task) {
  const dossierId = Number(selectedDossierId.value || 0)
  if (dossierId <= 0) {
    return
  }

  const state = ensureSelfIdentityTaskState(task)
  const newDisplayLabel = String(state.differentDraft || '').trim()
  if (!newDisplayLabel) {
    state.error = '请输入用于区分的新名称'
    return
  }

  state.error = ''
  state.dismissed = false
  state.submittingAction = 'confirm_different'

  try {
    await confirmDossierSelfIdentity(
      dossierId,
      buildSelfIdentityConfirmDifferentPayload(task, newDisplayLabel),
    )

    const refreshed = await refreshSelectedDossierAfterSelfIdentity(dossierId)
    if (refreshed) {
      ElMessage.success(`已创建新的当事人：${newDisplayLabel}`)
    } else {
      ElMessage.warning(`已创建新的当事人：${newDisplayLabel}，但部分视图刷新失败，请手动刷新`)
    }
  } catch (error) {
    console.error('Failed to confirm dossier self identity as different participant:', error)
    state.error = selfIdentityTaskErrorMessage(error, '确认失败')
  } finally {
    state.submittingAction = ''
  }
}

function coordinationEntityConversationCount(entity) {
  return normalizeGraphConversationIds(entity?.source_conversation_ids).length
}

function coordinationEntitySourceLabels(entity) {
  const labels = Array.isArray(entity?.source_labels) ? entity.source_labels : []
  return labels.map((item) => String(item || '').trim()).filter(Boolean)
}

function coordinationEntitySourceLabelText(entity) {
  const labels = coordinationEntitySourceLabels(entity)
  return labels.length ? labels.join(' / ') : '暂无来源称呼'
}

function coordinationEntityBestConversationLabel(entity) {
  const conversationId = Number(entity?.best_conversation_id || 0)
  return conversationId > 0 ? formatGraphConversationLabel(conversationId) : '暂无可跳转会话'
}

function coordinationEntityIdentityReason(entity) {
  const task = selectedDossierCoordinationIdentityTaskLookup.value[String(entity?.id || '').trim()]
  return String(task?.reason || '').trim()
}

function coordinationEntityHasBestConversation(entity) {
  return Number(entity?.best_conversation_id || 0) > 0
}

function selfAnchorIdentityTaskPrompt(task) {
  const conversationId = Number(task?.source_node?.conversation_id || 0)
  const rawLabel = String(task?.source_node?.raw_label || '当事人').trim() || '当事人'
  const targetLabel =
    String(task?.target_participant?.display_label || '已命名当事人').trim() || '已命名当事人'
  if (conversationId > 0) {
    return `会话 #${conversationId} 的 ${rawLabel} 是否也是 ${targetLabel}？`
  }
  return `${rawLabel} 是否也是 ${targetLabel}？`
}

async function handleOpenCoordinationEntityConversation(entity) {
  const conversationId = Number(entity?.best_conversation_id || 0)
  if (conversationId <= 0) {
    ElMessage.info('暂无可跳转会话')
    return
  }
  await switchConversation(conversationId)
}

function handleOpenCoordinationIdentity() {
  if (!selectedDossierPendingParticipantCount.value) {
    ElMessage.info('请到主体关系确认模块刷新后处理')
    return
  }
  openParticipantCandidateDialog()
}

async function handleOpenCoordinationNaming() {
  await nextTick()
  if (
    dossierCoordinationNamingRef.value &&
    typeof dossierCoordinationNamingRef.value.scrollIntoView === 'function'
  ) {
    dossierCoordinationNamingRef.value.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
    return
  }
  if (dossierIntelligenceState.error) {
    ElMessage.info('待命名任务暂不可用，请稍后刷新')
    return
  }
  ElMessage.info('请在下方命名确认区处理')
}

async function openDossierDrawer() {
  dossierDrawerVisible.value = true
  await refreshDossiers({ preferredDossierId: currentConversation.value?.dossier_id })
  if (selectedDossierId.value) {
    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
  }
}

async function handleCreateDossier() {
  const title = String(dossierForm.title || '').trim()
  if (!title) {
    ElMessage.warning('请输入案卷标题')
    return
  }

  dossierSaving.value = true
  try {
    const { data } = await createDossier({
      title,
      case_type: String(dossierForm.case_type || '').trim() || undefined,
      summary: String(dossierForm.summary || '').trim() || undefined,
    })
    if (data?.dossier) {
      upsertDossierLocal(data.dossier)
      await loadSelectedDossierDetail(data.dossier.id, { silent: true })
    } else {
      await refreshDossiers({ silent: true })
      if (selectedDossierId.value) {
        await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
      }
    }
    resetDossierForm()
    ElMessage.success('案卷已创建，可在下方绑定当前会话')
  } catch (error) {
    console.error('Failed to create dossier:', error)
    ElMessage.error('创建案卷失败')
  } finally {
    dossierSaving.value = false
  }
}

async function handleAttachDossier(dossier) {
  if (!currentConversationId.value || !dossier?.id) return

  try {
    await ElMessageBox.confirm(
      '请确认当前会话属于该案卷。若将不同案情的会话绑定到同一案卷，可能导致案卷摘要和图谱混杂。',
      '绑定案卷确认',
      {
        confirmButtonText: '确认绑定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.error('Failed to confirm dossier attachment:', error)
    ElMessage.error('绑定案卷确认失败')
    return
  }

  dossierBindingId.value = dossier.id
  try {
    const { data } = await attachConversationToDossier(currentConversationId.value, dossier.id)
    if (data?.conversation) {
      upsertConversationLocal(data.conversation)
      if (currentConversationId.value === data.conversation.id) {
        currentSessionId.value = data.conversation.session_id || currentSessionId.value
      }
    }
    await refreshConversationList(currentConversationId.value)
    await refreshDossiers({ silent: true, preferredDossierId: dossier.id })
    await loadSelectedDossierDetail(dossier.id, { silent: true })
    ElMessage.success('当前会话已绑定到案卷')
    await maybePromptParticipantResolutionAfterAttachment(dossier)
  } catch (error) {
    console.error('Failed to attach conversation to dossier:', error)
    ElMessage.error('绑定案卷失败')
  } finally {
    dossierBindingId.value = null
  }
}

async function maybePromptParticipantResolutionAfterAttachment(dossier) {
  const pendingCount = selectedDossierPendingParticipantCount.value
  if (!pendingCount) return

  dossierParticipantCandidateDialogVisible.value = true

  const firstCandidate = selectedDossierParticipantCandidates.value[0]
  const rawLabels = firstCandidate?.raw_labels?.join(' / ') || firstCandidate?.suggested_label || '新主体'
  const existingLabel = String(
    firstCandidate?.recommended_participant_label || firstCandidate?.existing_participant_label || ''
  ).trim()
  const tip =
    pendingCount === 1
      ? isCrossConversationCandidate(firstCandidate)
        ? existingLabel
          ? `新绑定会话中的实体可能与案卷内已确认主体「${existingLabel}」是同一主体。请确认是否合并。`
          : '新绑定会话中出现一条跨会话主体线索。请确认两个会话中的实体是否为同一主体。'
        : existingLabel
          ? `新会话中出现“${rawLabels}”，可能与案卷中的“${existingLabel}”是同一主体。请确认是否合并。`
          : `新会话中出现“${rawLabels}”这个新称呼。请确认它是否属于已有主体，或新建为独立主体。`
      : `新绑定会话中出现 ${pendingCount} 条待确认主体线索。请优先确认跨会话中的同一主体关系。`

  ElMessage.warning(tip)
}

async function handleSelectDossier(dossier) {
  if (!dossier?.id) return
  await loadSelectedDossierDetail(dossier.id)
}

async function handleArchiveDossier(dossier) {
  if (!dossier?.id) return

  try {
    await ElMessageBox.confirm(
      '归档后不会删除历史会话和主体信息，可在归档筛选中查看。',
      '归档案卷确认',
      {
        confirmButtonText: '确认归档',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.error('Failed to confirm dossier archive:', error)
    ElMessage.error('归档确认失败')
    return
  }

  try {
    await archiveDossier(dossier.id)
    if (selectedDossierId.value === dossier.id) {
      dossierParticipantCandidateDialogVisible.value = false
    }
    await refreshDossiers({ silent: true })
    if (dossierStatusFilter.value === 'active' && selectedDossierId.value === dossier.id) {
      selectedDossierId.value = filteredDossiers.value[0]?.id || null
    }
    if (selectedDossierId.value) {
      await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    } else {
      applySelectedDossierDetail(null)
      resetDossierGraphState()
    }
    ElMessage.success('案卷已归档')
  } catch (error) {
    console.error('Failed to archive dossier:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '归档案卷失败')
  }
}

async function handleHardDeleteDossier(dossier) {
  if (!dossier?.id) return

  const deletingSelectedDossier = selectedDossierId.value === dossier.id

  try {
    await ElMessageBox.confirm(
      '删除后案卷、主体确认关系和案卷摘要会被移除。历史聊天不会默认删除，但会与该案卷解绑。',
      '删除案卷确认',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.error('Failed to confirm dossier hard delete:', error)
    ElMessage.error('删除案卷确认失败')
    return
  }

  try {
    const { data } = await hardDeleteDossier(dossier.id)
    dossierParticipantCandidateDialogVisible.value = false
    await refreshConversationList(currentConversationId.value)
    await refreshDossiers({ silent: true })
    if (deletingSelectedDossier) {
      selectedDossierId.value = null
      applySelectedDossierDetail(null)
      resetDossierGraphState()
    } else if (selectedDossierId.value) {
      await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    } else {
      applySelectedDossierDetail(null)
      resetDossierGraphState()
    }
    const detachedCount = Number(data?.detached_conversations || 0)
    ElMessage.success(
      detachedCount > 0
        ? `案卷已删除，已解绑 ${detachedCount} 个历史会话`
        : '案卷已删除'
    )
  } catch (error) {
    console.error('Failed to hard delete dossier:', error)
    const detail = error?.response?.data?.detail || error?.message || '删除案卷失败'
    ElMessage.error(typeof detail === 'string' && detail ? detail : '删除案卷失败')
  }
}

async function handleUpdateDossierDetail() {
  if (!selectedDossierId.value) return

  const title = String(dossierDetailForm.title || '').trim()
  if (!title) {
    ElMessage.warning('Please enter a dossier title')
    return
  }

  dossierDetailSaving.value = true
  try {
    const { data } = await updateDossier(selectedDossierId.value, {
      title,
      case_type: String(dossierDetailForm.case_type || '').trim() || null,
      summary: String(dossierDetailForm.summary || '').trim() || null,
      status: dossierDetailForm.status || 'active',
    })

    if (data?.dossier) {
      upsertDossierLocal(data.dossier)
    }

    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    ElMessage.success('Dossier updated')
  } catch (error) {
    console.error('Failed to update dossier detail:', error)
    ElMessage.error('Failed to update dossier')
  } finally {
    dossierDetailSaving.value = false
  }
}

async function handleUpdateDossierParticipants() {
  if (!selectedDossierId.value) return

  const primaryLabel = String(dossierParticipantForm.primary_label || '').trim()
  const primaryRole = String(dossierParticipantForm.primary_role || '').trim()
  const opposingLabel = String(dossierParticipantForm.opposing_label || '').trim()
  const opposingRole = String(dossierParticipantForm.opposing_role || '').trim()

  if (!primaryLabel || !primaryRole || !opposingLabel || !opposingRole) {
    ElMessage.warning('请先补全当事人称呼和角色')
    return
  }

  dossierParticipantsSaving.value = true
  try {
    const { data } = await updateDossierParticipants(selectedDossierId.value, {
      participant_aliases: buildParticipantAliasesPayload(dossierParticipantForm),
    })

    if (data?.dossier) {
      upsertDossierLocal(data.dossier)
    }

    await refreshDossiers({ silent: true, preferredDossierId: selectedDossierId.value })
    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    ElMessage.success('当事人称呼已更新')
  } catch (error) {
    console.error('Failed to update dossier participants:', error)
    ElMessage.error('保存当事人称呼失败')
  } finally {
    dossierParticipantsSaving.value = false
  }
}

async function refreshSelectedDossierParticipantViews(options = {}) {
  if (!selectedDossierId.value) return
  await refreshDossiers({ silent: true, preferredDossierId: selectedDossierId.value })
  await loadSelectedDossierDetail(selectedDossierId.value, {
    silent: true,
    loadParticipants: false,
    loadGraph: false,
    loadCoordinationGraph: false,
    loadIntelligence: false,
  })
  await loadSelectedDossierParticipants(selectedDossierId.value, { silent: true })
  await loadSelectedDossierParticipantCandidates(selectedDossierId.value, { silent: true })
  await loadSelectedDossierGraph(selectedDossierId.value, { silent: true })
  await loadSelectedDossierCoordinationGraph(selectedDossierId.value, { silent: true })
  await loadSelectedDossierIntelligence(selectedDossierId.value)
  if (!selectedDossierParticipantCandidates.value.length) {
    dossierParticipantCandidateDialogVisible.value = false
  }
  if (!options.silent) {
    ElMessage.success('主体确认已更新')
  }
}

async function handleRefreshParticipantTasks() {
  if (!selectedDossierId.value) return
  clearDismissedParticipantCandidates()
  await Promise.allSettled([
    loadSelectedDossierParticipants(selectedDossierId.value, { silent: true }),
    loadSelectedDossierParticipantCandidates(selectedDossierId.value, { silent: true }),
    loadSelectedDossierCoordinationGraph(selectedDossierId.value, { silent: true }),
    loadSelectedDossierIntelligence(selectedDossierId.value),
  ])
  ElMessage.success('主体线索已刷新')
}

function handleOpenParticipantDebugPanel() {
  openParticipantAdvancedPanel('participants')
}

async function handleResolveSpecificCandidateToExisting(candidate) {
  const key = candidateKey(candidate)
  const participantId = key ? Number(dossierParticipantCandidateSelections[key] || 0) : 0
  if (participantId <= 0) {
    ElMessage.warning('请先选择要映射的已有主体')
    return
  }
  await handleResolveParticipantCandidate(candidate, { participantId })
}

async function handleResolveCrossConversationCandidate(candidate) {
  const participantId = crossCandidateExistingParticipantId(candidate)
  if (participantId > 0) {
    await handleResolveParticipantCandidate(candidate, { participantId })
    return
  }
  if (canCreateParticipantFromCandidate(candidate)) {
    await handleResolveParticipantCandidate(candidate)
    return
  }
  ElMessage.warning(candidateActionBlockedReason(candidate) || '当前线索还不能直接确认，请查看原因说明')
}

async function handleResolveParticipantCandidateCreate(candidate) {
  const label = String(dossierParticipantCandidateCreateDraft.label || '').trim()
  if (!label) {
    ElMessage.warning('请先填写独立主体称呼')
    return
  }
  await handleResolveParticipantCandidate(candidate, {
    participant: {
      label,
      role: String(dossierParticipantCandidateCreateDraft.role || '').trim() || null,
      party_side: dossierParticipantCandidateCreateDraft.party_side || 'unknown',
      entity_type: dossierParticipantCandidateCreateDraft.entity_type || 'unknown',
      description: String(dossierParticipantCandidateCreateDraft.description || '').trim() || null,
    },
  })
}

async function handleResolveGenericCandidateToSelf(candidate) {
  const selfParticipant = selectedDossierSelfAnchorParticipants.value[0] || null
  if (!selfParticipant?.id) {
    openParticipantAdvancedPanel('create')
    ElMessage.warning('当前还没有“我”这个已确认主体，请先在高级详情中创建“我”')
    return
  }
  await handleResolveParticipantCandidate(candidate, { participantId: selfParticipant.id })
}

async function handleResolveGenericCandidateToOpponent(candidate) {
  const opponentParticipants = selectedDossierOpponentParticipants.value
  if (!opponentParticipants.length) {
    openParticipantAdvancedPanel('create')
    ElMessage.warning('当前还没有可映射的对方主体，请先在高级详情中创建')
    return
  }

  const key = candidateKey(candidate)
  const selectedParticipantId = key ? Number(dossierParticipantCandidateSelections[key] || 0) : 0
  if (selectedParticipantId > 0) {
    await handleResolveParticipantCandidate(candidate, { participantId: selectedParticipantId })
    return
  }

  if (opponentParticipants.length === 1) {
    const opponent = opponentParticipants[0]
    try {
      await ElMessageBox.confirm(
        `将「${candidateRawLabel(candidate)}」映射到对方主体「${participantDisplayLabel(opponent)}」？`,
        '确认指向对方',
        {
          confirmButtonText: '确认',
          cancelButtonText: '取消',
          type: 'warning',
        }
      )
    } catch (error) {
      if (error === 'cancel' || error === 'close') return
      throw error
    }
    await handleResolveParticipantCandidate(candidate, { participantId: opponent.id })
    return
  }

  ElMessage.info('当前有多个对方主体，请先在卡片中选择一个再点击“指对方”')
}

function handleMarkNonEntityCandidate(candidate) {
  handleIgnoreParticipantCandidate(candidate, {
    message: `已将「${candidateRawLabel(candidate)}」暂记为非具体主体，本轮不做绑定`,
  })
}

function handleRejectCrossConversationCandidate(candidate) {
  handleIgnoreParticipantCandidate(candidate, {
    // TODO: persist cross-conversation not_same decisions when backend supports negative links.
    message: `已将「${formatGraphNodeLabel(candidate?.source_node)} / ${formatGraphNodeLabel(candidate?.target_node)}」标记为暂不同主体`,
  })
}

function handleUseMatchCandidate(candidate, participantId) {
  const key = candidateKey(candidate)
  if (!key) return
  dossierParticipantCandidateSelections[key] = Number(participantId || 0) || null
}

async function handleCreateResolvedParticipant() {
  if (!selectedDossierId.value) return

  const label = String(dossierParticipantCreateForm.label || '').trim()
  if (!label) {
    ElMessage.warning('请先填写主体称呼')
    return
  }

  dossierParticipantActionLoading.value = true
  try {
    await createDossierParticipant(selectedDossierId.value, {
      label,
      role: String(dossierParticipantCreateForm.role || '').trim() || null,
      party_side: dossierParticipantCreateForm.party_side || 'unknown',
      entity_type: dossierParticipantCreateForm.entity_type || 'unknown',
      description: String(dossierParticipantCreateForm.description || '').trim() || null,
    })
    resetDossierParticipantCreateForm()
    await refreshSelectedDossierParticipantViews({ silent: true })
    ElMessage.success('已新增案卷主体')
  } catch (error) {
    console.error('Failed to create dossier participant:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '新增主体失败')
  } finally {
    dossierParticipantActionLoading.value = false
  }
}

async function handleUpdateResolvedParticipant(participant) {
  if (!selectedDossierId.value || !participant?.id) return

  dossierParticipantActionLoading.value = true
  try {
    await updateDossierParticipant(selectedDossierId.value, participant.id, {
      label: String(participant.label || '').trim(),
      role: String(participant.role || '').trim() || null,
      party_side: participant.party_side || 'unknown',
      entity_type: participant.entity_type || 'unknown',
      description: String(participant.description || '').trim() || null,
    })
    await refreshSelectedDossierParticipantViews({ silent: true })
    ElMessage.success('主体信息已更新')
  } catch (error) {
    console.error('Failed to update dossier participant:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '更新主体失败')
  } finally {
    dossierParticipantActionLoading.value = false
  }
}

async function handleResolveParticipantCandidate(candidate, options = {}) {
  const key = candidateKey(candidate)
  if (!selectedDossierId.value || !key) return

  const participantId = options.participantId || dossierParticipantCandidateSelections[key]
  const mentions = candidateResolutionMentions(candidate)
  if (!mentions.length) {
    ElMessage.warning('当前主体线索没有可确认的图谱节点')
    return
  }

  const payload = {
    mentions: mentions.map((mention) => buildMentionFromResolutionNode(mention)),
  }

  const createParticipant = options.participant || null
  if (participantId) {
    payload.participant_id = participantId
  } else {
    if (createParticipant) {
      payload.participant = createParticipant
    } else {
    payload.participant = {
      label: candidate.suggested_label || candidate.raw_labels?.[0] || '未命名主体',
      role: candidate.suggested_role || null,
      party_side: candidate.party_side || 'unknown',
      entity_type: candidate.entity_type || 'unknown',
      description: null,
    }
    }
  }

  dossierParticipantActionLoading.value = true
  try {
    await resolveDossierParticipants(selectedDossierId.value, payload)
    delete dossierParticipantCandidateDismissed[key]
    resetParticipantCandidateCreateDraft()
    await refreshSelectedDossierParticipantViews({ silent: true })
    ElMessage.success(
      participantId
        ? (isCrossConversationCandidate(candidate) ? '跨会话主体已完成确认' : '称呼已映射到已有主体')
        : (isCrossConversationCandidate(candidate) ? '已为跨会话实体新建独立主体' : '称呼已新建为独立主体并完成确认')
    )
  } catch (error) {
    console.error('Failed to resolve dossier participant candidate:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '确认主体失败')
  } finally {
    dossierParticipantActionLoading.value = false
  }
}

function handleIgnoreParticipantCandidate(candidate, options = {}) {
  const key = candidateKey(candidate)
  if (!key) return
  dossierParticipantCandidateDismissed[key] = true
  if (dossierParticipantCandidateCreateKey.value === key) {
    resetParticipantCandidateCreateDraft()
  }
  nextTick(() => {
    if (!selectedDossierParticipantCandidates.value.length) {
      dossierParticipantCandidateDialogVisible.value = false
    }
  })
  ElMessage.info(options.message || '已暂不处理该主体线索，刷新后可重新查看')
}

async function handleGenerateDossierSummary() {
  if (!selectedDossierId.value) return

  dossierSummaryGenerating.value = true
  try {
    const { data } = await generateDossierSummary(selectedDossierId.value)
    await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    await refreshDossiers({ silent: true, preferredDossierId: selectedDossierId.value })
    if (typeof data?.warning === 'string' && data.warning) {
      ElMessage.warning(data.warning)
    } else {
      ElMessage.success('智能摘要已更新')
    }
  } catch (error) {
    console.error('Failed to generate dossier summary:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '智能摘要生成失败')
  } finally {
    dossierSummaryGenerating.value = false
  }
}

async function handleCreateConversationFromSelectedDossier() {
  if (!selectedDossierId.value) return
  await createNewConversation({ dossierId: selectedDossierId.value })
}

async function handleConfirmTrackedOnboarding() {
  const conversationId = trackedOnboardingState.conversationId
  if (!conversationId) return

  const title = String(trackedOnboardingForm.title || '').trim()
  const primaryLabel = String(trackedOnboardingForm.primary_label || '').trim()
  const primaryRole = String(trackedOnboardingForm.primary_role || '').trim()
  const opposingLabel = String(trackedOnboardingForm.opposing_label || '').trim()
  const opposingRole = String(trackedOnboardingForm.opposing_role || '').trim()

  if (!title || !primaryLabel || !primaryRole || !opposingLabel || !opposingRole) {
    ElMessage.warning('请先补全案卷名称和当事人称呼')
    return
  }

  trackedOnboardingSubmitting.value = true
  try {
    const conversation =
      conversations.value.find((item) => item.id === conversationId) || currentConversation.value
    let dossierId = trackedOnboardingState.dossierId
    const boundDossier =
      (dossierId && selectedDossierDetail.value?.id === dossierId
        ? selectedDossierDetail.value
        : dossiers.value.find((item) => item.id === dossierId)) || null

    if (!dossierId) {
      const { data } = await createDossier({
        title,
        case_type: String(conversation?.case_type || '').trim() || undefined,
      })
      if (!data?.dossier?.id) {
        throw new Error('Failed to create dossier')
      }
      dossierId = data.dossier.id
      trackedOnboardingState.dossierId = dossierId
      upsertDossierLocal(data.dossier)
    } else if (!boundDossier || title !== (boundDossier.title || '')) {
      const { data } = await updateDossier(dossierId, {
        title,
      })
      if (data?.dossier) {
        upsertDossierLocal(data.dossier)
      }
    }

    const { data: participantsData } = await updateDossierParticipants(dossierId, {
      participant_aliases: buildParticipantAliasesPayload(trackedOnboardingForm),
    })
    if (participantsData?.dossier) {
      upsertDossierLocal(participantsData.dossier)
    }

    const { data: consentData } = await setConversationModeConsent(conversationId, {
      mode: 'TRACKED_CASE',
      consent: 'tracked',
      reason: 'user_selected_tracked',
    })
    if (consentData?.conversation) {
      upsertConversationLocal(consentData.conversation)
    }

    if (conversation?.dossier_id !== dossierId) {
      const { data: attachData } = await attachConversationToDossier(conversationId, dossierId)
      if (attachData?.conversation) {
        upsertConversationLocal(attachData.conversation)
      }
    }

    await refreshConversationList(conversationId)
    await refreshDossiers({ silent: true, preferredDossierId: dossierId })
    await loadSelectedDossierDetail(dossierId, { silent: true })
    finalizeTrackedOnboarding({ cancelled: false })
    clearTrackedOnboardingState()
  } catch (error) {
    console.error('Failed to complete tracked onboarding:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '案卷初始化失败')
  } finally {
    trackedOnboardingSubmitting.value = false
  }
}

async function createNewConversation(options = {}) {
  try {
    const payload = {}
    if (options.dossierId) {
      payload.dossier_id = options.dossierId
    }

    const { data } = await createConversation(payload)
    const conversation = data.conversation
    conversationSearchKeyword.value = ''
    sessionBatchMode.value = false
    upsertConversationLocal(conversation)
    await switchConversation(conversation.id)
    if (options.dossierId) {
      await refreshDossiers({ silent: true, preferredDossierId: options.dossierId })
      await loadSelectedDossierDetail(options.dossierId, { silent: true })
    }
    if (!options.silent) {
      ElMessage.success('已创建新会话。')
    }
  } catch (error) {
    console.error('Failed to create conversation:', error)
    ElMessage.error('创建会话失败。')
  }
}

async function switchConversation(conversationId) {
  if (!conversationId) return
  stopGraphRefreshPolling()
  try {
    stopGraphRefreshPolling()
    resetAnalysis()
    resetGraph()
    const { data } = await getConversationMessages(conversationId)
    applyConversationPayload(data)
    await fetchConversationGraph(conversationId)
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    ElMessage.error('加载会话消息失败。')
  }
}

async function promptRenameConversation(session) {
  if (!session?.id) return

  try {
    const { value } = await ElMessageBox.prompt('请输入新的会话标题', '重命名会话', {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputValue: session.title || '',
      inputPattern: /\S+/,
      inputErrorMessage: '标题不能为空',
    })

    const title = value.trim()
    const { data } = await updateConversationTitle(session.id, title)
    upsertConversationLocal(data.conversation)
    if (currentConversationId.value === session.id) {
      currentSessionId.value = data.conversation.session_id || currentSessionId.value
    }
    ElMessage.success('会话标题已更新。')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    console.error('Failed to rename conversation:', error)
    ElMessage.error('更新会话标题失败。')
  }
}

async function handleConversationSearch(keyword) {
  conversationSearchKeyword.value = keyword || ''
  await refreshConversationList(currentConversationId.value)
}

function handleSessionBatchModeChange(enabled) {
  sessionBatchMode.value = Boolean(enabled)
}

async function handleBatchDelete(conversationIds) {
  return handleBatchDeleteV2(conversationIds)
  if (!conversationIds?.length) return

  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${conversationIds.length} 个会话吗？该操作会同时尝试清理对应案情图谱。`,
      '批量删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    return
  }

  try {
    const { data } = await batchDeleteConversations(conversationIds)
    const deletedIds = new Set(data.deleted || [])
    const currentDeleted = currentConversationId.value != null && deletedIds.has(currentConversationId.value)

    sessionBatchMode.value = false
    await refreshConversationList(currentDeleted ? null : currentConversationId.value)

    if (currentDeleted) {
      const nextConversation = conversations.value[0]
      if (nextConversation) {
        await switchConversation(nextConversation.id)
      } else {
        clearCurrentConversationState()
        await createNewConversation({ silent: true })
      }
    }

    if (Array.isArray(data.graph_warnings) && data.graph_warnings.length) {
      console.warn('Batch graph cleanup warnings:', data.graph_warnings)
      ElMessage.warning('部分图谱清理失败，请稍后重试或查看日志')
    }

    if (Array.isArray(data.skipped) && data.skipped.length) {
      ElMessage.warning(`已跳过 ${data.skipped.length} 个无效会话`)
    } else if ((data.deleted || []).length) {
      ElMessage.success(`已删除 ${data.deleted.length} 个会话`)
    }
  } catch (error) {
    console.error('Failed to batch delete conversations:', error)
    ElMessage.error('批量删除会话失败')
  }
}

async function removeConversation(conversationId) {
  return removeConversationV2(conversationId)
  if (!conversationId) return

  ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除确认', { type: 'warning' })
    .then(async () => {
      try {
        const { data } = await deleteConversation(conversationId)
        removeConversationLocal(conversationId)

        if (conversationId === currentConversationId.value) {
          const nextConversation = conversations.value[0]
          if (nextConversation) {
            await switchConversation(nextConversation.id)
          } else {
            clearCurrentConversationState()
            await createNewConversation({ silent: true })
          }
        }

        ElMessage.success('会话已删除。')
      } catch (error) {
        console.error('Failed to delete conversation:', error)
        ElMessage.error('删除会话失败。')
      }
    })
    .catch(() => {})
}

async function fetchConversationGraph(conversationId, query = '', options = {}) {
  if (!conversationId) {
    resetGraph()
    return
  }

  const requestId = ++latestGraphRequestId.value
  const preserveData = Boolean(options.preserveData)
  currentGraph.query = query || currentConversation.value?.title || `会话 ${conversationId} 案情图谱`
  if (!preserveData) {
    currentGraph.nodes = []
    currentGraph.edges = []
    currentGraph.tokens = []
    currentGraph.generatedAt = ''
  }
  currentGraph.loading = !options.silent
  currentGraph.error = ''

  try {
    const { data } = await getConversationGraph(conversationId)
    if (requestId !== latestGraphRequestId.value) return
    currentGraph.query = data.query || query || currentGraph.query
    currentGraph.nodes = data.nodes || []
    currentGraph.edges = data.edges || []
    currentGraph.tokens = data.tokens || []
    currentGraph.generatedAt = data.generated_at || ''
  } catch (error) {
    if (requestId !== latestGraphRequestId.value) return
    currentGraph.error = error?.response?.data?.detail || '图谱加载失败。'
  } finally {
    if (requestId === latestGraphRequestId.value && !options.silent) {
      currentGraph.loading = false
    }
  }
}

async function ensureConversationTitle(userText) {
  if (!currentConversation.value) return
  if ((currentConversation.value.title || '').trim()) return

  const title = userText.slice(0, 15).trim()
  if (!title) return

  touchConversationLocal(currentConversation.value.id, { title })
  try {
    const { data } = await updateConversationTitle(currentConversation.value.id, title)
    upsertConversationLocal(data.conversation)
  } catch (error) {
    console.error('Failed to set first message title:', error)
  }
}

function startGraphRefreshPolling(conversationId, previousNodeCount, query = '') {
  stopGraphRefreshPolling()
  if (!conversationId) return
  setGraphSyncState('running', '已触发图谱同步检查，等待后端任务完成')

  graphRefreshTimer.value = setInterval(async () => {
    try {
      await fetchConversationGraph(conversationId, query, { preserveData: true, silent: true })
      if ((currentGraph.nodes?.length || 0) !== previousNodeCount) {
        stopGraphRefreshPolling({
          status: 'success',
          summary: '图谱同步已完成，本轮节点变更已刷新到侧栏',
        })
      }
    } catch (error) {
      console.error('Graph refresh polling failed:', error)
      stopGraphRefreshPolling({
        status: 'warning',
        summary: '图谱同步检查失败，请稍后手动刷新图谱',
      })
    }
  }, 3000)

  graphRefreshStopTimer.value = setTimeout(() => {
    stopGraphRefreshPolling({
      status: 'warning',
      summary: '暂未观察到新的图谱变更，可能仍在后台处理或本轮无结构化更新',
    })
  }, 15000)
}

function updatePendingMessage(tempId, patch = {}) {
  const target = messages.value.find((item) => item.id === tempId)
  if (!target) return
  Object.assign(target, patch)
}

function removeMessageById(messageId) {
  messages.value = messages.value.filter((item) => item.id !== messageId)
}

async function createCaseForResend() {
  const { data } = await createConversation({})
  const conversation = data.conversation
  upsertConversationLocal(conversation)
  await switchConversation(conversation.id)
  ElMessage.success('已为新法律问题建立独立卷宗。')
  return conversation
}

async function resolveCaseBoundarySuggestion(message) {
  try {
    await ElMessageBox.confirm(
      message || '发现您似乎开启了一个新的法律问题，是否新建独立卷宗？',
      '检测到新案件',
      {
        confirmButtonText: '是，新建卷宗',
        cancelButtonText: '否，继续在当前补充',
        distinguishCancelAndClose: true,
        type: 'warning',
      }
    )
    return 'new_case'
  } catch (error) {
    if (error === 'cancel') {
      return 'stay_current'
    }
    if (error === 'close') {
      return 'cancelled'
    }
    throw error
  }
}

function conversationModeLabel(mode) {
  switch (String(mode || '').toUpperCase()) {
    case 'LEGAL_QA':
      return '法律问答'
    case 'CASE_ANALYSIS':
      return '案情分析'
    case 'TRACKED_CASE':
      return '持续跟踪'
    default:
      return '未分类'
  }
}

function conversationModeTagType(mode) {
  switch (String(mode || '').toUpperCase()) {
    case 'LEGAL_QA':
      return 'success'
    case 'CASE_ANALYSIS':
      return 'warning'
    case 'TRACKED_CASE':
      return 'danger'
    default:
      return 'info'
  }
}

function dossierStatusLabel(status) {
  switch (String(status || '').toLowerCase()) {
    case 'archived':
      return '已归档'
    case 'closed':
      return '已关闭'
    default:
      return '进行中'
  }
}

function dossierStatusTagType(status) {
  switch (String(status || '').toLowerCase()) {
    case 'archived':
      return 'info'
    case 'closed':
      return 'danger'
    default:
      return 'success'
  }
}

function persistenceConsentLabel(consent) {
  switch (String(consent || '').toLowerCase()) {
    case 'tracked':
      return '持续跟踪'
    case 'temporary':
      return '仅本次分析'
    default:
      return '未确认'
  }
}

function formatDossierConversationTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function dossierIntelligenceSummarySourceLabel(source) {
  switch (String(source || '').trim().toLowerCase()) {
    case 'auto_summary':
      return 'AI 自动摘要'
    case 'manual_summary':
      return '手动摘要'
    case 'rule_fallback':
      return '规则兜底'
    default:
      return '未标注来源'
  }
}

function formatDossierIntelligenceEvidenceList(value) {
  if (!Array.isArray(value) || !value.length) {
    return '暂无'
  }
  return value.map((item) => String(item)).join(' / ')
}

function formatDossierIntelligenceEvidenceJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch (error) {
    return '{}'
  }
}

function dossierIntelligenceTraceStepLabel(step) {
  switch (String(step || '').trim().toLowerCase()) {
    case 'load_dossier':
      return '加载案卷'
    case 'load_display_graph':
      return '加载展示图谱'
    case 'skip_display_graph':
      return '跳过展示图谱'
    case 'build_summary':
      return '构建摘要'
    case 'build_confirmation_tasks':
      return '构建确认任务'
    default:
      return step || '未知步骤'
  }
}

function dossierIntelligenceTraceStepStatusLabel(status) {
  switch (String(status || '').trim().toLowerCase()) {
    case 'success':
      return '成功'
    case 'warning':
      return '警告'
    case 'skipped':
      return '已跳过'
    default:
      return status || '未知状态'
  }
}

function formatParticipantAliasSummary(alias) {
  const label = String(alias?.label || '').trim()
  const role = String(alias?.role || '').trim()
  if (label && role) return `${label} / ${role}`
  if (label) return label
  if (role) return role
  return '未设置'
}

function formatGraphConversationLabel(conversationId) {
  if (conversationId === null || conversationId === undefined || conversationId === '') {
    return '会话未标注'
  }
  return `会话 #${conversationId}`
}

function normalizeGraphConversationIds(value) {
  const values = Array.isArray(value) ? value : [value]
  const conversationIds = []
  for (const item of values) {
    const conversationId = Number(item || 0)
    if (conversationId > 0 && !conversationIds.includes(conversationId)) {
      conversationIds.push(conversationId)
    }
  }
  return conversationIds
}

function formatGraphConversationListLabel(conversationIds) {
  const normalizedConversationIds = normalizeGraphConversationIds(conversationIds)
  if (!normalizedConversationIds.length) {
    return '会话未标注'
  }
  return normalizedConversationIds.map((conversationId) => formatGraphConversationLabel(conversationId)).join(' / ')
}

function formatGraphPropertiesTitle(properties) {
  if (!properties || typeof properties !== 'object') return ''

  return Object.entries(properties)
    .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
    .slice(0, 6)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join('\n')
}

function participantDisplayLabel(participant) {
  if (!participant || typeof participant !== 'object') return '未命名主体'
  const label = String(participant.label || '').trim()
  if (label) return label
  const participantId = Number(participant.id || 0)
  return participantId > 0 ? `主体 #${participantId}` : '未命名主体'
}

function matchCandidateDisplayLabel(match) {
  if (!match || typeof match !== 'object') return '未命名主体'
  const displayLabel = String(match.display_label || match.participant_label || '').trim()
  if (displayLabel) return displayLabel
  const participantId = Number(match.participant_id || 0)
  return participantId > 0 ? `主体 #${participantId}` : '未命名主体'
}

function candidateKey(candidate) {
  if (!candidate || typeof candidate !== 'object') return ''
  return String(candidate.task_key || candidate.candidate_key || candidate.raw_label || '').trim()
}

function candidateTaskType(candidate) {
  return String(candidate?.task_type || 'raw_label_resolution').trim() || 'raw_label_resolution'
}

function isCrossConversationCandidate(candidate) {
  return candidateTaskType(candidate) === 'cross_conversation_entity_resolution'
}

function sortParticipantCandidates(candidates) {
  return [...candidates].sort((left, right) => {
    const leftTaskType = candidateTaskType(left)
    const rightTaskType = candidateTaskType(right)
    if (leftTaskType !== rightTaskType) {
      return leftTaskType === 'cross_conversation_entity_resolution' ? -1 : 1
    }

    const leftLabelKind = candidateLabelKind(left)
    const rightLabelKind = candidateLabelKind(right)
    if (leftLabelKind !== rightLabelKind) {
      const priority = { specific: 0, mixed: 1, generic: 2 }
      return (priority[leftLabelKind] ?? 9) - (priority[rightLabelKind] ?? 9)
    }

    return candidateRawLabel(left).localeCompare(candidateRawLabel(right), 'zh-CN')
  })
}

function candidateRawLabel(candidate) {
  if (!candidate || typeof candidate !== 'object') return '未命名称呼'
  const rawLabel = String(candidate.raw_label || '').trim()
  if (rawLabel) return rawLabel
  const rawLabels = Array.isArray(candidate.raw_labels)
    ? candidate.raw_labels.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  if (rawLabels.length) return rawLabels.join(' / ')
  return String(candidate.suggested_label || '').trim() || '未命名称呼'
}

function candidateLabelKind(candidate) {
  const labelKind = String(candidate?.label_kind || '').trim().toLowerCase()
  if (labelKind === 'generic') return 'generic'
  if (labelKind === 'mixed') return 'mixed'
  return 'specific'
}

function candidateLabelKindLabel(candidate) {
  switch (candidateLabelKind(candidate)) {
    case 'generic':
      return '泛称'
    case 'mixed':
      return '跨类线索'
    default:
      return '具体称呼'
  }
}

function candidateLabelKindTagType(candidate) {
  switch (candidateLabelKind(candidate)) {
    case 'generic':
      return 'warning'
    case 'mixed':
      return 'success'
    default:
      return 'primary'
  }
}

function confidenceLabel(confidence) {
  switch (String(confidence || '').trim().toLowerCase()) {
    case 'high':
      return '高'
    case 'medium':
      return '中'
    case 'low':
      return '低'
    default:
      return ''
  }
}

function candidateConfidenceLabel(candidate) {
  return confidenceLabel(candidate?.confidence)
}

function formatGraphNodeType(nodeType) {
  switch (String(nodeType || '').trim().toLowerCase()) {
    case 'organization':
      return '组织'
    case 'company':
      return '公司'
    case 'employer':
      return '用工主体'
    case 'person':
      return '自然人'
    case 'authority':
      return '机构'
    case 'vehicle':
      return '车辆'
    case 'event':
      return '事件'
    case 'fact':
      return '事实'
    case 'evidence':
      return '证据'
    case 'document':
      return '文书'
    case 'amount':
    case 'money':
      return '金额'
    case 'time':
    case 'date':
      return '时间'
    case 'law':
    case 'statute':
      return '法条'
    case 'claim':
      return '诉求'
    case 'procedure':
      return '程序'
    case 'issue':
      return '争点'
    default:
      return '未分类'
  }
}

function graphNodePrimaryLabel(node) {
  if (!node || typeof node !== 'object') return ''
  const displayLabel = String(node.display_label || '').trim()
  if (displayLabel) return displayLabel
  const label = String(node.label || node.name || '').trim()
  if (label) return label
  const graphNodeLabel = String(node.graph_node_label || '').trim()
  if (graphNodeLabel) return graphNodeLabel
  const rawLabel = String(node.raw_label || '').trim()
  if (rawLabel) return rawLabel
  const nodeId = String(node.id || '').trim()
  return nodeId ? `Node ${nodeId}` : '未标注节点'
}

function graphNodeConversationIds(node) {
  if (!node || typeof node !== 'object') return []
  return normalizeGraphConversationIds([
    ...(Array.isArray(node.source_conversation_ids) ? node.source_conversation_ids : []),
    ...(Array.isArray(node.conversation_ids) ? node.conversation_ids : []),
    node.conversation_id,
    node.properties?.conversation_id,
  ])
}

function graphNodeRawLabels(node) {
  if (!node || typeof node !== 'object') return []
  const labels = [
    ...(Array.isArray(node.raw_labels) ? node.raw_labels : []),
    node.raw_label,
    node.graph_node_label,
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)

  return labels.filter((label, index) => labels.indexOf(label) === index)
}

function graphNodeRawLabelsSummary(node) {
  const primaryLabel = graphNodePrimaryLabel(node)
  const rawLabels = graphNodeRawLabels(node)
  const extraLabels = rawLabels.filter((label) => label !== primaryLabel)
  if (extraLabels.length) return extraLabels.join(' / ')
  return rawLabels.length > 1 ? rawLabels.join(' / ') : ''
}

function formatGraphPreviewNodeKind(node) {
  const kind = String(node?.kind || '').trim().toLowerCase()
  if (kind === 'participant') return '已确认主体'
  if (kind === 'raw_entity') {
    const entityType = formatGraphNodeType(node?.entity_type || node?.type || node?.graph_node_type)
    return node?.pending_confirmation ? `待确认主体 · ${entityType}` : `原始主体 · ${entityType}`
  }
  if (kind === 'raw_event') return '事件节点'
  if (kind === 'raw_other') {
    return `其他节点 · ${formatGraphNodeType(node?.entity_type || node?.type || node?.graph_node_type)}`
  }
  return formatGraphNodeType(node?.entity_type || node?.type || node?.graph_node_type)
}

function formatGraphNodeLabel(node) {
  if (!node || typeof node !== 'object') return '未标注节点'
  const graphNodeLabel = String(node.graph_node_label || '').trim()
  if (graphNodeLabel) return graphNodeLabel
  const rawLabel = String(node.raw_label || '').trim()
  if (rawLabel) return rawLabel
  const graphNodeId = String(node.graph_node_id || '').trim()
  if (graphNodeId) return `节点 ${graphNodeId}`
  return '未标注节点'
}

function candidateCardTitle(candidate) {
  if (isCrossConversationCandidate(candidate)) {
    return '确认跨会话主体'
  }
  return candidateRawLabel(candidate)
}

function candidateCardSubtitle(candidate) {
  if (isCrossConversationCandidate(candidate)) {
    return '这两个会话中的主体是同一个吗？'
  }
  return candidateMentionsConversationLabel(candidate)
}

function candidateRecommendedParticipantLabel(candidate) {
  if (!candidate || typeof candidate !== 'object') return ''
  const recommendedLabel = String(candidate.recommended_participant_label || '').trim()
  if (recommendedLabel) return recommendedLabel
  const matchCandidate = candidateMatchCandidates(candidate)[0] || null
  return matchCandidate ? matchCandidateDisplayLabel(matchCandidate) : ''
}

function crossCandidateExistingParticipantId(candidate) {
  if (!isCrossConversationCandidate(candidate)) return 0
  const key = candidateKey(candidate)
  const selectedParticipantId = key ? Number(dossierParticipantCandidateSelections[key] || 0) : 0
  if (selectedParticipantId > 0) return selectedParticipantId
  return Number(candidate?.recommended_participant_id || candidate?.existing_participant_id || 0) || 0
}

function buildMentionFromResolutionNode(node) {
  return {
    conversation_id: node?.conversation_id,
    raw_label: String(node?.raw_label || node?.graph_node_label || '').trim(),
    source: node?.graph_node_id ? 'graph_node' : String(node?.source || 'graph'),
    confidence: typeof node?.confidence === 'number' ? node.confidence : null,
    graph_node_id: String(node?.graph_node_id || '').trim() || null,
    graph_node_label: String(node?.graph_node_label || '').trim() || null,
    graph_node_type: String(node?.graph_node_type || '').trim() || null,
  }
}

function candidateResolutionMentions(candidate) {
  if (isCrossConversationCandidate(candidate)) {
    return [candidate?.source_node, candidate?.target_node].filter(
      (item) => item && typeof item === 'object'
    )
  }
  return Array.isArray(candidate?.mentions)
    ? candidate.mentions.filter((item) => item && typeof item === 'object')
    : []
}

function canCreateParticipantFromCandidate(candidate) {
  if (isCrossConversationCandidate(candidate)) {
    return Boolean(candidate?.can_create_new)
  }
  return Boolean(candidate?.can_create_new)
}

function candidateActionBlockedReason(candidate) {
  if (!isCrossConversationCandidate(candidate)) return ''
  const mentions = candidateResolutionMentions(candidate)
  if (mentions.length < 2) return '缺少图谱节点来源'
  if (candidateLabelKind(candidate) === 'generic') {
    return '该称呼过于泛化，需要先选择已有主体'
  }
  if (crossCandidateExistingParticipantId(candidate) > 0 || canCreateParticipantFromCandidate(candidate)) {
    return ''
  }
  if (!candidate?.can_link_existing && !candidate?.can_create_new) {
    return '该节点不是可确认主体'
  }
  return '该线索暂时不能确认，请先选择已有主体或等待更多跨会话证据'
}

function candidateIntroText(candidate) {
  if (isCrossConversationCandidate(candidate)) {
    const sourceLabel = formatGraphNodeLabel(candidate?.source_node)
    const targetLabel = formatGraphNodeLabel(candidate?.target_node)
    return `会话中的「${sourceLabel}」与「${targetLabel}」可能指向同一个主体，请确认是否需要合并。`
  }
  const rawLabel = candidateRawLabel(candidate)
  if (candidateLabelKind(candidate) === 'generic') {
    return `新会话中出现「${rawLabel}」。这是泛称，系统不能直接确定它指谁。`
  }
  return `新会话中出现「${rawLabel}」。它可能是已有主体的另一个称呼，也可能是新的独立主体。`
}

function candidateMentionsConversationLabel(candidate) {
  const mentions = Array.isArray(candidate?.mentions) ? candidate.mentions : []
  const conversationIds = [...new Set(
    mentions
      .map((mention) => Number(mention?.conversation_id || 0))
      .filter((conversationId) => conversationId > 0)
  )]
  if (!conversationIds.length) return '未标注会话'
  return conversationIds.map((conversationId) => `会话 #${conversationId}`).join(' / ')
}

function candidateMatchCandidates(candidate) {
  return Array.isArray(candidate?.match_candidates)
    ? candidate.match_candidates.filter((item) => item && typeof item === 'object')
    : []
}

function candidateLinkOptions(candidate) {
  const labelKind = candidateLabelKind(candidate)
  if (labelKind === 'generic') {
    return selectedDossierOpponentParticipants.value
  }
  return selectedDossierResolvedParticipants.value
}

function resetParticipantCandidateCreateDraft() {
  dossierParticipantCandidateCreateKey.value = ''
  dossierParticipantCandidateCreateDraft.label = ''
  dossierParticipantCandidateCreateDraft.role = ''
  dossierParticipantCandidateCreateDraft.party_side = 'unknown'
  dossierParticipantCandidateCreateDraft.entity_type = 'unknown'
  dossierParticipantCandidateCreateDraft.description = ''
}

function openParticipantAdvancedPanel(sectionName = '') {
  dossierAdvancedSections.value = ['advanced']
  if (sectionName) {
    ensureDossierParticipantSection(sectionName)
  }
}

function clearDismissedParticipantCandidates() {
  Object.keys(dossierParticipantCandidateDismissed).forEach((key) => {
    delete dossierParticipantCandidateDismissed[key]
  })
}

function startParticipantCandidateCreate(candidate) {
  const key = candidateKey(candidate)
  if (!key) return
  if (dossierParticipantCandidateCreateKey.value === key) {
    resetParticipantCandidateCreateDraft()
    return
  }

  dossierParticipantCandidateCreateKey.value = key
  dossierParticipantCandidateCreateDraft.label = String(
    candidate?.suggested_label || candidateRawLabel(candidate)
  ).trim()
  dossierParticipantCandidateCreateDraft.role = String(candidate?.suggested_role || '').trim()
  dossierParticipantCandidateCreateDraft.party_side = String(candidate?.party_side || 'unknown').trim() || 'unknown'
  dossierParticipantCandidateCreateDraft.entity_type = String(candidate?.entity_type || 'unknown').trim() || 'unknown'
  dossierParticipantCandidateCreateDraft.description = ''
}

function isParticipantCandidateCreateOpen(candidate) {
  return dossierParticipantCandidateCreateKey.value === candidateKey(candidate)
}

function buildParticipantAliasesPayload(form) {
  return {
    primary_party: {
      label: String(form.primary_label || '').trim(),
      role: String(form.primary_role || '').trim(),
      party_side: 'claimant',
    },
    opposing_party: {
      label: String(form.opposing_label || '').trim(),
      role: String(form.opposing_role || '').trim(),
      party_side: 'opponent',
    },
  }
}

function resolveTrackedOnboardingDossierSeed(conversationId) {
  const conversation =
    conversations.value.find((item) => item.id === conversationId) || currentConversation.value
  const boundDossierId = conversation?.dossier_id || null
  const boundDossier =
    (boundDossierId && selectedDossierDetail.value?.id === boundDossierId
      ? selectedDossierDetail.value
      : dossiers.value.find((item) => item.id === boundDossierId)) || null

  const defaults = defaultParticipantDrafts(conversation?.case_type || '')
  trackedOnboardingState.dossierId = boundDossierId
  trackedOnboardingForm.title = boundDossier?.title || defaults.title
  applyParticipantAliasesToForm(
    trackedOnboardingForm,
    boundDossier?.participant_aliases || {},
    conversation?.case_type || ''
  )
}

function finalizeTrackedOnboarding(result) {
  trackedOnboardingVisible.value = false
  const resolver = trackedOnboardingResolver
  trackedOnboardingResolver = null
  if (resolver) {
    resolver(result)
  }
}

async function resolveTrackedOnboarding(options = {}) {
  trackedOnboardingState.conversationId = options.conversationId || currentConversationId.value
  trackedOnboardingState.text = options.text || ''
  trackedOnboardingState.forceCurrentCase = Boolean(options.forceCurrentCase)
  trackedOnboardingState.draftText = options.draftText || ''
  resolveTrackedOnboardingDossierSeed(trackedOnboardingState.conversationId)
  trackedOnboardingVisible.value = true
  return new Promise((resolve) => {
    trackedOnboardingResolver = resolve
  })
}

function handleCancelTrackedOnboarding() {
  if (trackedOnboardingSubmitting.value) return
  finalizeTrackedOnboarding({ cancelled: true })
  clearTrackedOnboardingState()
}

async function resolveModeConsentSuggestion(prompt) {
  const message = [
    prompt || '我注意到你正在描述一个具体案情。是否要保存为持续跟踪的案情会话？',
    '',
    '当前聊天记录仍会保存在会话历史中；选择持续跟踪后，系统会把案情整理为可持续维护的结构化案情。',
  ].join('\n')

  try {
    await ElMessageBox.confirm(
      message,
      '模式确认',
      {
        confirmButtonText: '保存并持续跟踪',
        cancelButtonText: '仅本次分析',
        distinguishCancelAndClose: true,
        type: 'warning',
      }
    )
    return 'tracked'
  } catch (error) {
    if (error === 'cancel') {
      return 'temporary'
    }
    if (error === 'close') {
      return 'cancelled'
    }
    throw error
  }
}

function restoreDraftInput(draftText, feedbackMessage = '', feedbackType = 'info') {
  inputText.value = draftText
  if (!feedbackMessage) return

  if (feedbackType === 'error') {
    ElMessage.error(feedbackMessage)
    return
  }

  ElMessage.info(feedbackMessage)
}

async function sendImpl(textOverride = null, options = {}) {
  if (!systemStatus.ready) {
    ElMessage.warning('系统仍在初始化，请稍后。')
    return
  }

  const baseConversationId = options.conversationId || currentConversationId.value
  if (!baseConversationId) {
    ElMessage.warning('请先新建或选择一份卷宗。')
    return
  }

  const finalStr = (typeof textOverride === 'string' ? textOverride : inputText.value) || ''
  const text = finalStr.trim()
  if (!text || sending.value) return
  const draftText = text

  const forceCurrentCase = Boolean(options.forceCurrentCase)
  const modeConsentEnabled =
    options.modeConsentEnabled === false
      ? false
      : !isTrackedDossierConversation(resolveConversationRecord(baseConversationId))
  const restoreDraftOnErrorText =
    typeof options.restoreDraftOnErrorText === 'string' ? options.restoreDraftOnErrorText : null
  const tempKey = `temp-user-${Date.now()}`
  const nowIso = new Date().toISOString()
  const previousNodeCount = currentGraph.nodes?.length || 0

  messages.value.push({
    id: tempKey,
    key: tempKey,
    role: 'user',
    content: text,
    time: formatTime(nowIso),
    sources: [],
    agentActions: [],
    pending: true,
  })

  inputText.value = ''
  sending.value = true
  graphSidebarCollapsed.value = false
  touchConversationLocal(baseConversationId)
  resetAnalysis()
  currentAnalysis.requestState = 'running'
  currentAnalysis.updatedAt = nowIso
  setGraphSyncState('pending', '等待主请求完成后检查图谱同步状态')
  currentGraph.loading = true
  currentGraph.error = ''
  currentGraph.query = text
  scrollBottom()

  try {
    const initRes = await sendMessage(text, baseConversationId, {
      forceCurrentCase,
      isPreciseMode: proactiveGraphMode.value,
      proactiveGraphMode: proactiveGraphMode.value,
      modeConsentEnabled,
    })
    const initData = initRes?.data || {}

    if (initData.action === 'suggest_new_case') {
      removeMessageById(tempKey)
      const nextAction = await resolveCaseBoundarySuggestion(initData.message)
      sending.value = false
      currentGraph.loading = false
      resetAnalysis()

      if (nextAction === 'new_case') {
        try {
          const newConversation = await createCaseForResend()
          if (!newConversation?.id) {
            restoreDraftInput(draftText, '新案情确认失败，输入内容已恢复', 'error')
            return
          }

          await sendImpl(text, {
            conversationId: newConversation.id,
            forceCurrentCase: false,
            modeConsentEnabled: false,
            restoreDraftOnErrorText: draftText,
          })
        } catch (error) {
          console.error('Failed to continue suggest_new_case flow:', error)
          restoreDraftInput(draftText, '新案情确认失败，输入内容已恢复', 'error')
        }
        return
      }

      if (nextAction === 'cancelled') {
        restoreDraftInput(draftText, '已取消，输入内容已恢复')
        return
      }

      await sendImpl(text, {
        conversationId: baseConversationId,
        forceCurrentCase: true,
        modeConsentEnabled: false,
        restoreDraftOnErrorText: draftText,
      })
      return
    }

    if (initData.action === 'mode_consent') {
      removeMessageById(tempKey)
      const nextAction = await resolveModeConsentSuggestion(initData.consent_prompt)
      sending.value = false
      currentGraph.loading = false
      resetAnalysis()

      if (nextAction === 'cancelled') {
        restoreDraftInput(draftText, '已取消，输入内容已恢复')
        return
      }

      if (nextAction === 'tracked') {
        const onboardingResult = await resolveTrackedOnboarding({
          conversationId: baseConversationId,
          text,
          forceCurrentCase,
          draftText,
        })
        if (onboardingResult?.cancelled) {
          restoreDraftInput(draftText, '已取消，输入内容已恢复')
          return
        }

        await sendImpl(text, {
          conversationId: baseConversationId,
          forceCurrentCase,
          modeConsentEnabled: false,
          restoreDraftOnErrorText: draftText,
        })
        return
      }

      const payload =
        nextAction === 'tracked'
          ? {
              mode: 'TRACKED_CASE',
              consent: 'tracked',
              reason: 'user_selected_tracked',
            }
          : {
              mode: 'CASE_ANALYSIS',
              consent: 'temporary',
              reason: 'user_selected_temporary',
            }

      try {
        const { data: consentData } = await setConversationModeConsent(baseConversationId, payload)
        if (consentData?.conversation) {
          upsertConversationLocal(consentData.conversation)
        }
      } catch (error) {
        console.error('Failed to persist mode consent:', error)
        restoreDraftInput(draftText, '模式确认失败，输入内容已恢复', 'error')
        return
      }

      await sendImpl(text, {
        conversationId: baseConversationId,
        forceCurrentCase,
        modeConsentEnabled: false,
        restoreDraftOnErrorText: draftText,
      })
      return
    }

    const data = initData.task_id ? (await initRes).data : initData
    updatePendingMessage(tempKey, { pending: false })
    currentAnalysis.requestState = 'error'
    currentAnalysis.requestError = ''
    currentAnalysis.updatedAt = new Date().toISOString()
    setGraphSyncState('error', '主请求失败，未完成图谱同步')

    messages.value.push({
      id: `assistant-${Date.now()}`,
      key: `assistant-${Date.now()}`,
      role: 'assistant',
      content: data.response || '未返回有效内容。',
      time: formatTime(new Date().toISOString()),
      createdAt: new Date().toISOString(),
      sources: data.sources || [],
      agentActions: data.agent_actions || [],
      routeIntent: data.route_intent || '',
      latencyMs: data.latency_ms ?? null,
      confidenceScore: data.confidence_score ?? null,
      missingInfo: data.missing_info || [],
      pending: false,
    })

    applyCurrentAnalysisResponse(data)
    setGraphSyncState('running', '主请求已完成，正在检查图谱更新任务')
    void ensureConversationTitle(text)
    await refreshConversationList(baseConversationId)
    await fetchConversationGraph(baseConversationId, text)
    startGraphRefreshPolling(baseConversationId, previousNodeCount, text)
  } catch (error) {
    console.error('Failed to send message:', error)
    if (restoreDraftOnErrorText) {
      restoreDraftInput(restoreDraftOnErrorText, '发送失败，输入内容已恢复', 'error')
    }
    updatePendingMessage(tempKey, { pending: false })
    currentAnalysis.requestState = 'error'
    currentAnalysis.requestError = error?.message || '未知错误'
    currentAnalysis.updatedAt = new Date().toISOString()
    setGraphSyncState('error', '主请求失败，未完成图谱同步')
    messages.value.push({
      id: `assistant-error-${Date.now()}`,
      key: `assistant-error-${Date.now()}`,
      role: 'assistant',
      content: `请求失败：${error.message || '未知错误'}`,
      time: formatTime(new Date().toISOString()),
      createdAt: new Date().toISOString(),
      sources: [],
      agentActions: [],
      routeIntent: '',
      latencyMs: null,
      confidenceScore: null,
      missingInfo: [],
      pending: false,
    })
  } finally {
    sending.value = false
    currentGraph.loading = false
    scrollBottom()
  }
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void sendImpl()
  }
}

async function handleSummary() {
  if (!currentConversationId.value) return
  stopGraphRefreshPolling()

  summaryLoading.value = true
  try {
    const { data } = await getConversationSummary(currentConversationId.value)
    messages.value.push({
      id: `summary-${Date.now()}`,
      key: `summary-${Date.now()}`,
      role: 'assistant',
      content: `会话摘要：\n${data.summary}`,
      time: formatTime(new Date().toISOString()),
      sources: [],
      agentActions: [],
      pending: false,
    })
    scrollBottom()
    ElMessage.success('摘要生成完成。')
  } catch (error) {
    console.error('Failed to summarize conversation:', error)
    ElMessage.error('生成摘要失败。')
  } finally {
    summaryLoading.value = false
  }
}

function handleExport() {
  if (!currentConversationId.value) return

  const payload = {
    conversation_id: currentConversationId.value,
    session_id: currentSessionId.value,
    messages: messages.value,
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `会话记录_${currentConversationId.value}.json`
  link.click()
  URL.revokeObjectURL(link.href)
  ElMessage.success('会话已导出。')
}

function handleLogout() {
  clearAuthSession()
  ElMessage.success('已退出登录。')
  router.replace('/login')
}

function handleClear() {
  return handleClearReset()
  if (!currentConversationId.value) return

  ElMessageBox.confirm('确定清空当前会话的全部消息吗？', '清空确认', { type: 'warning' })
    .then(async () => {
      try {
        await clearConversationMessages(currentConversationId.value)
        messages.value = []
        resetAnalysis()
        resetGraph()
        ElMessage.success('当前会话已清空。')
      } catch (error) {
        console.error('Failed to clear conversation:', error)
        ElMessage.error('清空会话失败。')
      }
    })
    .catch(() => {})
}

function handleClearLegacy() {
  return handleClearReset()
  if (!currentConversationId.value) return

  ElMessageBox.confirm(
    '这会删除当前会话的聊天记录，并重置案情摘要、结构化案情状态和案情图谱。确定继续吗？',
    '清空聊天并重置案情',
    {
      confirmButtonText: '继续',
      cancelButtonText: '取消',
      type: 'warning',
    }
  )
    .then(async () => {
      try {
        const { data } = await clearConversationMessages(currentConversationId.value)
        messages.value = []
        resetAnalysis()
        resetGraph()
        touchConversationLocal(currentConversationId.value, {
          case_summary: '',
          case_type: '',
          case_state: null,
          conversation_mode: 'UNKNOWN',
          persistence_consent: 'unknown',
          mode_confirmed_at: null,
          mode_reason: null,
        })
        if (data?.warning) {
          ElMessage.warning('聊天已清空，但图谱清理可能未完成。')
        } else {
          ElMessage.success('聊天已清空，案情状态已重置。')
        }
      } catch (error) {
        console.error('Failed to clear conversation:', error)
        ElMessage.error('清空当前会话失败')
      }
    })
    .catch(() => {})
}

async function handleBatchDeleteV2(conversationIds) {
  if (!conversationIds?.length) return

  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${conversationIds.length} 个会话吗？该操作会同时尝试清理对应案情图谱。`,
      '批量删除确认',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
  } catch (error) {
    return
  }

  try {
    const { data } = await batchDeleteConversations(conversationIds)
    const deletedIds = new Set(data.deleted || [])
    const currentDeleted = currentConversationId.value != null && deletedIds.has(currentConversationId.value)

    sessionBatchMode.value = false
    await refreshConversationList(currentDeleted ? null : currentConversationId.value)

    if (currentDeleted) {
      const nextConversation = conversations.value[0]
      if (nextConversation) {
        await switchConversation(nextConversation.id)
      } else {
        clearCurrentConversationState()
      }
    }

    if (Array.isArray(data.graph_warnings) && data.graph_warnings.length) {
      console.warn('Batch graph cleanup warnings:', data.graph_warnings)
      ElMessage.warning('部分图谱清理失败，请稍后重试或查看日志')
    }

    if (Array.isArray(data.skipped) && data.skipped.length) {
      ElMessage.warning(`已跳过 ${data.skipped.length} 个无效会话`)
    } else if ((data.deleted || []).length) {
      ElMessage.success(`已删除 ${data.deleted.length} 个会话`)
    }
  } catch (error) {
    console.error('Failed to batch delete conversations:', error)
    ElMessage.error('批量删除会话失败')
  }
}

async function removeConversationV2(conversationId) {
  if (!conversationId) return

  ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除确认', { type: 'warning' })
    .then(async () => {
      try {
        const { data } = await deleteConversation(conversationId)
        removeConversationLocal(conversationId)

        if (conversationId === currentConversationId.value) {
          const nextConversation = conversations.value[0]
          if (nextConversation) {
            await switchConversation(nextConversation.id)
          } else {
            clearCurrentConversationState()
          }
        }

        if (data?.warning) {
          ElMessage.warning('会话已删除，但图谱清理可能未完成。')
        } else {
          ElMessage.success('会话已删除。')
        }
      } catch (error) {
        console.error('Failed to delete conversation:', error)
        ElMessage.error('删除会话失败。')
      }
    })
    .catch(() => {})
}

function handleClearReset() {
  if (!currentConversationId.value) return

  ElMessageBox.confirm(
    '这会删除当前会话的聊天记录，并重置案情摘要、结构化案情状态和案情图谱。确定继续吗？',
    '清空聊天并重置案情',
    {
      confirmButtonText: '继续',
      cancelButtonText: '取消',
      type: 'warning',
    }
  )
    .then(async () => {
      try {
        const { data } = await clearConversationMessages(currentConversationId.value)
        messages.value = []
        resetAnalysis()
        resetGraph()
        touchConversationLocal(currentConversationId.value, {
          case_summary: '',
          case_type: '',
          case_state: null,
          conversation_mode: 'UNKNOWN',
          persistence_consent: 'unknown',
          mode_confirmed_at: null,
          mode_reason: null,
        })
        if (data?.warning) {
          ElMessage.warning('聊天已清空，但图谱清理可能未完成。')
        } else {
          ElMessage.success('聊天已清空，案情状态已重置。')
        }
      } catch (error) {
        console.error('Failed to clear conversation:', error)
        ElMessage.error('清空当前会话失败')
      }
    })
    .catch(() => {})
}

async function handleFeedback({ rating, comment }) {
  try {
    await submitFeedback({
      session_id: currentSessionId.value,
      turn_index: messages.value.filter((message) => message.role === 'user').length,
      rating,
      comment,
      feedback_type: 'general',
    })
    ElMessage.success('反馈已提交。')
  } catch (error) {
    console.error('Failed to submit feedback:', error)
    ElMessage.error('提交反馈失败。')
  }
}

async function refreshSystemStatus() {
  try {
    const { data } = await getSystemStatus()
    systemStatus.status = data.status || 'loading'
    systemStatus.ready = Boolean(data.ready)
    systemStatus.healthy = Boolean(data.ready)
    systemStatus.components = {
      chroma: data.components?.chroma || 'loading',
      neo4j: data.components?.neo4j || 'loading',
      llm: data.components?.llm || 'loading',
      celery: data.components?.celery || 'loading',
    }
    systemStatus.ragReady = systemStatus.components.chroma === 'connected'
    systemStatus.docCount = data.knowledge?.total_doc_count || 0
    systemStatus.model = data.model || ''
    systemStatus.tools = data.tools || []

    if (systemStatus.ready) {
      stopSystemStatusPolling()
    }
  } catch (error) {
    console.error('Failed to fetch system status:', error)
    systemStatus.status = 'loading'
    systemStatus.ready = false
    systemStatus.healthy = false
  }
}

function startSystemStatusPolling() {
  void refreshSystemStatus()
  if (statusTimer.value) clearInterval(statusTimer.value)
  statusTimer.value = setInterval(refreshSystemStatus, 1000)
}

function stopSystemStatusPolling() {
  if (statusTimer.value) {
    clearInterval(statusTimer.value)
    statusTimer.value = null
  }
}

function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function scrollBottom() {
  nextTick(() => {
    if (scrollRef.value) {
      scrollRef.value.setScrollTop(messageListRef.value?.scrollHeight || 99999)
    }
  })
}
</script>

<style>
html,
body,
#app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
}
</style>

<style scoped>
.app-container {
  height: 100vh;
  background: #f5f7fa;
  padding-top: 44px;
}

.system-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  color: #fff;
}

.system-banner.loading {
  background: linear-gradient(90deg, #d97706, #f59e0b);
}

.system-banner.ready {
  background: linear-gradient(90deg, #15803d, #22c55e);
}

.system-banner-content {
  width: min(1320px, calc(100vw - 24px));
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  font-size: 13px;
}

.system-banner-main {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.system-banner-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  opacity: 0.92;
}

.banner-icon {
  font-size: 16px;
}

.rotating {
  animation: spin 1s linear infinite;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
  height: 56px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #303133;
}

.app-title {
  font-size: 16px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 700;
}

.clear-reset-button {
  font-size: 14px;
}

.clear-reset-button :deep(.el-icon) {
  font-size: 14px;
  margin-right: 4px;
}


.main-area {
  height: calc(100vh - 100px);
}

.side-panel {
  background: #fff;
  padding: 16px;
  overflow: hidden;
}

.left-panel {
  border-right: 1px solid #e4e7ed;
}

.right-panel {
  border-left: 1px solid #e4e7ed;
  transition: width 0.25s ease;
}

.right-panel.collapsed {
  padding-left: 10px;
  padding-right: 10px;
}

.chat-area {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.local-model-card {
  margin: 16px 24px 0;
  padding: 14px 16px;
  border: 1px solid #dbeafe;
  border-radius: 14px;
  background: linear-gradient(135deg, #eff6ff, #f8fafc);
}

.local-model-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.local-model-title {
  font-size: 14px;
  font-weight: 700;
  color: #1f2937;
}

.local-model-status {
  margin-top: 8px;
  font-size: 12px;
  font-weight: 600;
}

.local-model-status.is-off {
  color: #6b7280;
}

.local-model-status.is-on {
  color: #15803d;
}

.local-model-status.is-error {
  color: #b91c1c;
}

.conversation-mode-card {
  margin: 12px 24px 0;
  padding: 12px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
}

.conversation-mode-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.conversation-mode-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.conversation-mode-title {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.conversation-mode-subtitle {
  font-size: 12px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-mode-tags {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.conversation-dossier-row {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eef2f7;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.conversation-dossier-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.conversation-dossier-title {
  font-size: 12px;
  font-weight: 700;
  color: #111827;
}

.conversation-dossier-subtitle {
  font-size: 12px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dossier-drawer {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dossier-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-toolbar-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dossier-toolbar-title,
.dossier-section-title,
.dossier-current-title {
  font-size: 14px;
  font-weight: 700;
  color: #111827;
}

.dossier-toolbar-subtitle,
.dossier-current-hint {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}

.dossier-current-card,
.dossier-create-card,
.dossier-list-card {
  padding: 14px;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
}

.dossier-current-name {
  margin-top: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}

.dossier-create-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-list-status {
  font-size: 12px;
  color: #6b7280;
}

.dossier-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-list-filters {
  margin: 10px 0 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-status-filter {
  width: 100%;
}

.dossier-item {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dossier-item.active {
  border-color: #93c5fd;
  background: #eff6ff;
}

.dossier-item-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dossier-item-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dossier-item-title {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.dossier-item-meta {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #4b5563;
}

.dossier-item-meta-secondary {
  color: #6b7280;
}

.dossier-item-summary {
  font-size: 12px;
  line-height: 1.5;
  color: #6b7280;
}

.dossier-item-actions,
.dossier-detail-actions,
.dossier-inline-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dossier-detail-card {
  padding: 14px;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
}

.dossier-detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-detail-body {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier-detail-meta {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #4b5563;
}

.dossier-auto-summary-section {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dossier-coordination-section {
  padding: 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff, #f3f7ff);
}

.dossier-coordination-body {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier-coordination-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dossier-coordination-chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 600;
}

.dossier-coordination-group {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-coordination-entity-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-coordination-entity-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border-radius: 12px;
  background: #f8fbff;
  border: 1px solid #dbeafe;
}

.dossier-coordination-entity-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-coordination-entity-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.dossier-coordination-entity-title strong {
  font-size: 13px;
  color: #111827;
}

.dossier-coordination-entity-title span,
.dossier-coordination-entity-meta {
  font-size: 12px;
  color: #6b7280;
}

.dossier-coordination-entity-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  line-height: 1.6;
}

.dossier-coordination-entity-actions {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
}

.dossier-coordination-task-section {
  scroll-margin-top: 12px;
}

.dossier-coordination-empty {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px dashed #bfdbfe;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.7;
}

.dossier-intelligence-card {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dbeafe;
}

.dossier-intelligence-meta {
  margin-top: 8px;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: #6b7280;
}

.dossier-intelligence-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.dossier-intelligence-info-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dossier-intelligence-info-card span {
  font-size: 12px;
  color: #6b7280;
}

.dossier-intelligence-info-card strong {
  font-size: 13px;
  color: #111827;
  word-break: break-word;
}

.dossier-intelligence-note-list {
  margin: 10px 0 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #374151;
  font-size: 12px;
  line-height: 1.6;
}

.dossier-intelligence-empty {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px dashed #bfdbfe;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.6;
}

.dossier-intelligence-trace-list,
.dossier-intelligence-warning-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-intelligence-trace-item {
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #dbeafe;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.dossier-intelligence-trace-item strong {
  font-size: 12px;
  color: #111827;
}

.dossier-intelligence-trace-item span {
  font-size: 11px;
  color: #6b7280;
  white-space: nowrap;
}

.dossier-intelligence-task-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-intelligence-task-card {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid #bfdbfe;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-intelligence-task-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.dossier-intelligence-task-header strong {
  font-size: 13px;
  color: #111827;
}

.dossier-intelligence-task-type {
  font-size: 11px;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 999px;
  padding: 4px 8px;
}

.dossier-intelligence-task-meta,
.dossier-intelligence-task-evidence {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #4b5563;
  line-height: 1.6;
}

.dossier-intelligence-task-reason {
  font-size: 12px;
  color: #1f2937;
  line-height: 1.7;
}

.dossier-intelligence-task-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-intelligence-task-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #6b7280;
}

.dossier-intelligence-task-json {
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #dbeafe;
  color: #374151;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.dossier-participants-section {
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dossier-resolved-participants-section {
  padding: 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: #f8fbff;
}

.dossier-graph-section {
  padding: 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff, #eef6ff);
}

.dossier-auto-summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-inline-actions-spread {
  justify-content: space-between;
}

.dossier-participants-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-participant-summary-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.dossier-participant-summary-card {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dossier-participant-summary-card span {
  font-size: 12px;
  color: #6b7280;
}

.dossier-participant-summary-card strong {
  font-size: 20px;
  color: #1d4ed8;
}

.dossier-participant-task-entry {
  margin-top: 12px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid #bfdbfe;
  background: rgba(255, 255, 255, 0.94);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-participant-task-entry.is-empty {
  border-style: dashed;
}

.dossier-participant-task-entry-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dossier-participant-task-entry-copy strong {
  font-size: 14px;
  color: #111827;
}

.dossier-participant-task-entry-copy span {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-participant-smart-tip {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  font-size: 12px;
  line-height: 1.6;
}

.dossier-participant-pill-list {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.dossier-participant-pill {
  min-width: 0;
  max-width: 100%;
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dossier-participant-pill strong {
  font-size: 13px;
  color: #111827;
}

.dossier-participant-pill span {
  font-size: 12px;
  color: #6b7280;
}

.dossier-resolved-participants-body {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier-debug-collapse {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-participant-collapse {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-collapse-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.5;
}

.dossier-collapse-title strong {
  font-size: 13px;
  color: #111827;
}

.dossier-collapse-title span {
  font-size: 12px;
  color: #6b7280;
}

.dossier-resolved-participant-list,
.dossier-participant-candidate-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-resolved-participant-card,
.dossier-participant-create-card,
.dossier-participant-candidate-card {
  padding: 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
}

.dossier-resolved-participant-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.dossier-resolved-participant-actions,
.dossier-participant-candidate-actions {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #6b7280;
}

.dossier-participant-candidate-title {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.dossier-participant-candidate-meta {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-participant-task-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.dossier-participant-task-card {
  padding: 14px;
  border-radius: 14px;
  border: 1px solid #dbeafe;
  background: rgba(255, 255, 255, 0.96);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-participant-task-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dossier-participant-task-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dossier-participant-task-copy strong {
  font-size: 15px;
  color: #111827;
  word-break: break-word;
}

.dossier-participant-task-copy span {
  font-size: 12px;
  color: #6b7280;
}

.dossier-participant-task-tags {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dossier-participant-task-description {
  font-size: 13px;
  line-height: 1.7;
  color: #1f2937;
}

.dossier-participant-task-meta {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-participant-resolution-pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
}

.dossier-participant-resolution-node {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.dossier-participant-resolution-node-title {
  font-size: 11px;
  font-weight: 700;
  color: #1d4ed8;
}

.dossier-participant-resolution-node strong {
  font-size: 13px;
  color: #111827;
  word-break: break-word;
}

.dossier-participant-resolution-node span {
  font-size: 11px;
  line-height: 1.5;
  color: #6b7280;
  word-break: break-word;
}

.dossier-participant-resolution-arrow {
  align-self: center;
  font-size: 18px;
  color: #2563eb;
}

.dossier-participant-task-match-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-participant-task-match-title {
  font-size: 12px;
  font-weight: 700;
  color: #374151;
}

.dossier-participant-task-match-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dossier-participant-match-chip {
  min-width: 0;
  padding: 8px 10px;
  border-radius: 12px;
  border: 1px solid #dbeafe;
  background: #f8fafc;
  color: #374151;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  cursor: pointer;
}

.dossier-participant-match-chip.selected {
  border-color: #2563eb;
  background: #eff6ff;
}

.dossier-participant-match-chip strong {
  font-size: 12px;
  color: #111827;
}

.dossier-participant-match-chip span {
  font-size: 11px;
  color: #6b7280;
  line-height: 1.5;
}

.dossier-participant-inline-create-card {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
}

.dossier-participant-task-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #6b7280;
}

.dossier-graph-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-participants-grid,
.tracked-onboarding-grid {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.dossier-auto-summary-meta {
  margin-top: 8px;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: #6b7280;
}

.dossier-auto-summary-content {
  margin-top: 10px;
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
  color: #111827;
}

.dossier-graph-body {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-graph-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.dossier-graph-stat {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dossier-graph-stat-label {
  font-size: 12px;
  color: #6b7280;
}

.dossier-graph-stat-value {
  font-size: 20px;
  font-weight: 700;
  color: #1e3a8a;
}

.dossier-graph-meta {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: #4b5563;
}

.dossier-graph-warning,
.dossier-graph-error,
.dossier-graph-empty {
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.dossier-graph-warning {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fcd34d;
}

.dossier-graph-warning-soft {
  background: #eff6ff;
  color: #1d4ed8;
  border-color: #bfdbfe;
}

.dossier-graph-error {
  margin-top: 10px;
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.dossier-graph-empty {
  background: rgba(255, 255, 255, 0.82);
  color: #6b7280;
  border: 1px dashed #bfdbfe;
}

.dossier-graph-aliases {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.dossier-graph-alias-item {
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dossier-graph-alias-item span {
  font-size: 12px;
  color: #6b7280;
}

.dossier-graph-alias-item strong {
  font-size: 13px;
  color: #111827;
}

.dossier-graph-preview {
  padding: 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid #dbeafe;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-graph-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.dossier-graph-preview-note {
  font-size: 12px;
  color: #92400e;
  background: #fef3c7;
  border: 1px solid #fcd34d;
  border-radius: 999px;
  padding: 4px 8px;
}

.dossier-graph-preview-hint {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.dossier-graph-preview-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dossier-graph-preview-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-graph-preview-subtitle {
  font-size: 12px;
  font-weight: 700;
  color: #1f2937;
}

.dossier-graph-node-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dossier-graph-node-chip {
  min-width: 0;
  max-width: 100%;
  padding: 10px 12px;
  border-radius: 12px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dossier-graph-node-chip strong,
.dossier-graph-edge-source,
.dossier-graph-edge-target {
  font-size: 13px;
  color: #111827;
  word-break: break-word;
}

.dossier-graph-node-chip span,
.dossier-graph-edge-meta {
  font-size: 11px;
  color: #6b7280;
}

.dossier-graph-edge-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dossier-graph-edge-item {
  padding: 10px 12px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #dbeafe;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.dossier-graph-edge-relation {
  font-size: 12px;
  font-weight: 700;
  color: #1d4ed8;
  white-space: nowrap;
}

.dossier-graph-preview-empty,
.dossier-graph-preview-truncation {
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.dossier-graph-preview-empty {
  color: #6b7280;
  background: #f8fafc;
  border: 1px dashed #bfdbfe;
}

.dossier-graph-preview-truncation {
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
}

.tracked-onboarding-copy {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tracked-onboarding-title {
  font-size: 15px;
  font-weight: 700;
  color: #111827;
}

.tracked-onboarding-subtitle,
.tracked-onboarding-hint {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.tracked-onboarding-form {
  margin-top: 14px;
}

.tracked-onboarding-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.dossier-detail-conversations {
  padding-top: 12px;
  border-top: 1px solid #eef2f7;
}

.dossier-detail-conversations-title {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 700;
  color: #111827;
}

.dossier-conversation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dossier-conversation-item {
  padding: 10px 12px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
}

.dossier-conversation-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-conversation-title {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}

.dossier-conversation-time {
  font-size: 11px;
  color: #6b7280;
  flex: 0 0 auto;
}

.dossier-conversation-tags {
  margin-top: 8px;
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.chat-scroll {
  flex: 1;
  padding: 20px 24px;
}

.message-list {
  max-width: 800px;
  margin: 0 auto;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 260px;
  text-align: center;
  color: #6b7280;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  color: #1f2937;
  line-height: 1.4;
}

.empty-title {
  font-size: 0;
}

.empty-title::after {
  content: '新建卷宗';
  font-size: 18px;
  font-weight: 700;
  color: #1f2937;
}

.empty-subtitle {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.6;
  max-width: 420px;
}

.empty-subtitle {
  font-size: 0;
}

.empty-subtitle::after {
  content: '从左侧选择我的案件卷宗，或先新建一份独立卷宗后开始法律咨询。';
  font-size: 13px;
  line-height: 1.6;
  color: #6b7280;
}

.input-area {
  display: flex;
  gap: 12px;
  padding: 12px 24px 16px;
  background: #fff;
  border-top: 1px solid #e4e7ed;
  align-items: flex-end;
}

.input-area.locked {
  opacity: 0.86;
}

.input-area .el-textarea {
  flex: 1;
}

.input-stack {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mode-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mode-switch-label {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}

.input-area .el-button {
  height: 54px;
  min-width: 80px;
}

@media (max-width: 768px) {
  .dossier-graph-stats,
  .dossier-intelligence-grid,
  .dossier-graph-aliases,
  .dossier-participant-summary-grid,
  .dossier-participants-grid,
  .dossier-resolved-participant-grid,
  .tracked-onboarding-grid {
    grid-template-columns: 1fr;
  }

  .dossier-participant-resolution-pair {
    grid-template-columns: 1fr;
  }

  .dossier-participant-resolution-arrow {
    justify-self: center;
    transform: rotate(90deg);
  }

  .dossier-item-main,
  .dossier-detail-header,
  .dossier-auto-summary-header {
    flex-direction: column;
    align-items: stretch;
  }

  .dossier-coordination-entity-card {
    flex-direction: column;
  }

  .dossier-coordination-entity-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .dossier-graph-edge-item {
    grid-template-columns: 1fr;
  }
}

.init-overlay {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(245, 247, 250, 0.78);
  backdrop-filter: blur(4px);
}

.init-card {
  width: min(420px, calc(100vw - 64px));
  padding: 28px 24px;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.14);
  text-align: center;
}

.init-icon {
  font-size: 28px;
  color: #d97706;
}

.init-title {
  margin-top: 12px;
  font-size: 20px;
  font-weight: 700;
  color: #1f2937;
}

.init-subtitle {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: #6b7280;
}

.loading-msg {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.loading-msg .bubble-wrap {
  flex: 1;
}

.loading-bubble {
  display: inline-flex;
  gap: 4px;
  padding: 14px 20px;
  background: #f4f4f5;
  border-radius: 12px;
  border-top-left-radius: 4px;
}

.dot {
  width: 8px;
  height: 8px;
  background: #c0c4cc;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.dot:nth-child(1) {
  animation-delay: 0s;
}

.dot:nth-child(2) {
  animation-delay: 0.2s;
}

.dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%,
  80%,
  100% {
    transform: scale(0.6);
    opacity: 0.4;
  }

  40% {
    transform: scale(1);
    opacity: 1;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
