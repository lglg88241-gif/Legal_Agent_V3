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
    :before-close="handleCloseDossierDrawer"
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

          <DossierCanonicalDrawer
            :loading="dossierCanonicalGraphLoading"
            :intelligence-loading="dossierIntelligenceLoading"
            :graph-preview="selectedDossierCanonicalGraphPreview"
            :stats="selectedDossierCanonicalStats"
            :has-single-source-hint="selectedDossierCanonicalHasSingleSourceHint"
            :confirmed-entities="selectedDossierConfirmedCanonicalEntities"
            :cross-conversation-tasks="selectedDossierCanonicalCrossConversationTasks"
            :helpers="canonicalDrawerHelpers"
            @refresh-canonical="handleRefreshDossierCanonical"
            @open-graph-node-conversation="handleOpenCanonicalGraphNodeConversation"
            @open-entity-conversation="handleOpenCanonicalEntityConversation"
            @open-manual-merge="handleOpenManualMerge"
            @confirm-cross-conversation-same-entity="handleConfirmCrossConversationSameEntity"
            @reject-cross-conversation-same-entity="handleRejectCrossConversationSameEntity"
          />

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
    v-model="canonicalManualMergeDialogVisible"
    title="合并到其他实体"
    width="560px"
    :close-on-click-modal="!canonicalManualMergeSubmitting"
    :close-on-press-escape="!canonicalManualMergeSubmitting"
    @closed="resetCanonicalManualMergeDialog"
  >
    <div class="canonical-merge-dialog">
      <div class="canonical-merge-source">
        <span>来源实体</span>
        <strong>{{ canonicalManualMergeSourceLabel }}</strong>
        <small>{{ canonicalManualMergeSourceHint }}</small>
      </div>

      <el-select
        v-model="canonicalManualMergeTargetId"
        class="canonical-merge-target-select"
        filterable
        placeholder="选择要合并到的目标实体"
        :disabled="canonicalManualMergeSubmitting"
      >
        <el-option
          v-for="entity in canonicalManualMergeTargetOptions"
          :key="entity.id"
          :label="entity.label"
          :value="entity.id"
        >
          <div class="canonical-merge-option">
            <strong>{{ entity.label }}</strong>
            <span>{{ entity.meta }}</span>
          </div>
        </el-option>
      </el-select>

      <div v-if="canonicalManualMergeSelectedTarget" class="canonical-merge-preview">
        <span>目标实体</span>
        <strong>{{ canonicalManualMergeSelectedTarget.label }}</strong>
        <small>{{ canonicalManualMergeSelectedTarget.meta }}</small>
      </div>

      <el-alert
        v-if="canonicalManualMergeError"
        type="error"
        :title="canonicalManualMergeError"
        show-icon
        :closable="false"
      />
    </div>

    <template #footer>
      <div class="party-naming-actions">
        <el-button
          :disabled="canonicalManualMergeSubmitting"
          @click="canonicalManualMergeDialogVisible = false"
        >
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="canonicalManualMergeSubmitting"
          :disabled="!canonicalManualMergeTargetId"
          @click="submitCanonicalManualMerge"
        >
          确认合并
        </el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="partyNamingModalVisible"
    title="确认当前会话当事人"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    @closed="handlePartyNamingModalClosed"
  >
    <div class="party-naming-modal">
      <div class="party-naming-title">
        请确认当前会话中的“{{ activePartyNamingCurrentLabel }}”是谁？
      </div>
      <div class="party-naming-subtitle">
        这个确认只作用于当前会话的当事人代称，不会合并其他会话里的“我/当事人”。
      </div>

      <div class="party-naming-meta">
        <span>当前称呼</span>
        <div class="party-naming-tags">
          <el-tag
            v-for="label in activePartyNamingRawLabels"
            :key="`party-naming-raw-${label}`"
            size="small"
            effect="plain"
          >
            {{ label }}
          </el-tag>
          <span v-if="!activePartyNamingRawLabels.length">未标注</span>
        </div>
      </div>

      <div class="party-naming-meta">
        <span>建议称呼</span>
        <div class="party-naming-tags">
          <el-button
            v-for="label in activePartyNamingSuggestedLabels"
            :key="`party-naming-suggested-${label}`"
            size="small"
            text
            @click="partyNamingInput = label"
          >
            {{ label }}
          </el-button>
          <span v-if="!activePartyNamingSuggestedLabels.length">可直接填写真实姓名或代称</span>
        </div>
      </div>

      <el-input
        v-model="partyNamingInput"
        maxlength="50"
        clearable
        placeholder="例如：张三"
        :disabled="partyNamingSubmitting"
        @keyup.enter="handleConfirmPartyNamingModal"
      />

      <el-alert
        v-if="partyNamingError"
        class="party-naming-error"
        type="error"
        :title="partyNamingError"
        show-icon
        :closable="false"
      />
    </div>

    <template #footer>
      <div class="party-naming-actions">
        <el-button @click="handleDismissPartyNamingModal" :disabled="partyNamingSubmitting">
          暂不确认
        </el-button>
        <el-button
          type="primary"
          :loading="partyNamingSubmitting"
          :disabled="!partyNamingInput.trim()"
          @click="handleConfirmPartyNamingModal"
        >
          确认
        </el-button>
      </div>
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
      <div class="tracked-onboarding-title">为了后续持续跟踪，请确认案卷名称。</div>
      <div class="tracked-onboarding-subtitle">
        当事人和对方称呼可以暂不填写；当前会话当事人会在图谱更新后单独确认。
      </div>
      <div class="tracked-onboarding-subtitle">
        责任实体会从会话图谱自动进入案卷，不需要在这里手动补全。
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
          placeholder="主要当事人称呼（可选）"
        />
        <el-input
          v-model="trackedOnboardingForm.primary_role"
          maxlength="100"
          placeholder="主要当事人角色（可选）"
        />
        <el-input
          v-model="trackedOnboardingForm.opposing_label"
          maxlength="100"
          placeholder="对方称呼（可选）"
        />
        <el-input
          v-model="trackedOnboardingForm.opposing_role"
          maxlength="100"
          placeholder="对方角色（可选）"
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
import DossierCanonicalDrawer from './components/DossierCanonicalDrawer.vue'
import SessionList from './components/SessionList.vue'
import {
  archiveDossier,
  attachConversationToDossier,
  batchDeleteConversations,
  clearConversationMessages,
  confirmDossierPartyNaming,
  confirmDossierSelfIdentity,
  decideDossierCrossConversationResolution,
  manualMergeDossierCanonicalEntities,
  createConversation,
  createDossier,
  deleteConversation,
  generateDossierSummary,
  getConversationGraph,
  getConversationMessages,
  getConversationSummary,
  getConversations,
  getDossierCanonicalGraph,
  getDossierDetail,
  getDossierIntelligence,
  getDossiers,
  hardDeleteDossier,
  getLocalModelStatus,
  searchConversations,
  getSystemStatus,
  sendMessage,
  setConversationModeConsent,
  setLocalModelStatus,
  submitFeedback,
  updateDossier,
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
const graphRefreshInFlight = ref(false)
const handledGraphClarificationKeys = ref(new Set())
const graphClarificationPromptOpen = ref(false)
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
const DOSSIER_COORDINATION_GRAPH_EDGE_LIMIT = 28
const selectedDossierId = ref(null)
const selectedDossierDetail = ref(null)
const dossierDetailLoading = ref(false)
const dossierDetailSaving = ref(false)
const dossierSummaryGenerating = ref(false)
const dossierCanonicalGraphLoading = ref(false)
const dossierIntelligenceLoading = ref(false)
const dossierCanonicalCrossTaskSubmittingKey = ref('')
const canonicalManualMergeDialogVisible = ref(false)
const canonicalManualMergeSourceEntity = ref(null)
const canonicalManualMergeTargetId = ref('')
const canonicalManualMergeSubmitting = ref(false)
const canonicalManualMergeError = ref('')
const dossierPartyNamingSubmittingKey = ref('')
const dossierDrawerClosing = ref(false)
const partyNamingModalVisible = ref(false)
const activePartyNamingTask = ref(null)
const selfIdentityModalVisible = ref(false)
const activeSelfIdentityTask = ref(null)
const partyNamingInput = ref('')
const partyNamingSubmitting = ref(false)
const partyNamingError = ref('')
const dismissedPartyNamingTaskIds = ref(new Set())
const dossierSelfIdentityTaskStates = reactive({})
const trackedOnboardingVisible = ref(false)
const trackedOnboardingSubmitting = ref(false)
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
const dossierCanonicalGraphState = reactive({
  dossierId: null,
  entities: [],
  edges: [],
  tasks: [],
  dossierEntityState: null,
  stats: {
    boundConversations: 0,
    connectedEntities: 0,
    pendingIdentity: 0,
    pendingNaming: 0,
    isolatedAnchors: 0,
  },
  view: 'canonical',
  generatedAt: '',
  error: '',
})
const dossierPartyNamingDrafts = reactive({})
const PARTY_NAMING_SELF_ANCHOR_LABELS = new Set([
  '我',
  '本人',
  '当事人',
  '劳动者',
  '申请人',
  '我们',
])
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
  executionTrace: null,
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
const selectedDossierIntelligenceConfirmationTasks = computed(() =>
  Array.isArray(dossierIntelligenceState.confirmationTasks)
    ? dossierIntelligenceState.confirmationTasks
    : []
)
const selectedDossierCanonicalEntities = computed(() =>
  Array.isArray(dossierCanonicalGraphState.entities) ? dossierCanonicalGraphState.entities : []
)
const selectedDossierCanonicalTasks = computed(() =>
  Array.isArray(dossierCanonicalGraphState.tasks) ? dossierCanonicalGraphState.tasks : []
)
const selectedDossierResolvedCanonicalEntities = computed(() =>
  selectedDossierCanonicalEntities.value.filter(
    (entity) => String(entity?.status || '').trim() === 'canonical'
  )
)
const selectedDossierHasCanonicalGraph = computed(
  () => selectedDossierResolvedCanonicalEntities.value.length > 0
)
const selectedDossierPrimaryCanonicalEntities = computed(() =>
  selectedDossierHasCanonicalGraph.value
    ? selectedDossierResolvedCanonicalEntities.value
    : selectedDossierCanonicalEntities.value
)
const selectedDossierCanonicalCrossConversationTasks = computed(() =>
  selectedDossierCanonicalTasks.value.filter((task) => {
    const taskType = String(task?.task_type || task?.type || '').trim()
    return taskType === 'cross_conversation_same_entity'
  })
)
const selectedDossierCanonicalStats = computed(() => ({
  boundConversations: Number(dossierCanonicalGraphState.stats?.boundConversations || 0),
  connectedEntities: selectedDossierHasCanonicalGraph.value
    ? selectedDossierResolvedCanonicalEntities.value.length
    : Number(dossierCanonicalGraphState.stats?.connectedEntities || 0),
  pendingIdentity: selectedDossierHasCanonicalGraph.value
    ? selectedDossierCanonicalCrossConversationTasks.value.length
    : Number(dossierCanonicalGraphState.stats?.pendingIdentity || 0),
  pendingNaming: Number(dossierCanonicalGraphState.stats?.pendingNaming || 0),
  isolatedAnchors: Number(dossierCanonicalGraphState.stats?.isolatedAnchors || 0),
  relations: Array.isArray(dossierCanonicalGraphState.edges)
    ? dossierCanonicalGraphState.edges.length
    : 0,
}))
const selectedDossierConfirmedCanonicalEntities = computed(() =>
  selectedDossierHasCanonicalGraph.value
    ? selectedDossierResolvedCanonicalEntities.value
    : selectedDossierCanonicalEntities.value.filter(
        (entity) => String(entity?.status || '').trim() === 'confirmed'
      )
)
const canonicalManualMergeSourceId = computed(() =>
  canonicalEntityStableId(canonicalManualMergeSourceEntity.value)
)
const canonicalManualMergeSourceLabel = computed(() =>
  canonicalEntityDisplayLabel(canonicalManualMergeSourceEntity.value)
)
const canonicalManualMergeSourceHint = computed(() => {
  const sourceEntity = canonicalManualMergeSourceEntity.value
  if (!sourceEntity) return ''
  const conversationCount = canonicalEntityConversationCount(sourceEntity)
  const sourceLabels = canonicalEntitySourceLabelText(sourceEntity)
  return `${conversationCount} 个来源会话；来源称呼：${sourceLabels}`
})
const canonicalManualMergeTargetOptions = computed(() =>
  selectedDossierConfirmedCanonicalEntities.value
    .map((entity) => {
      const id = canonicalEntityStableId(entity)
      if (!id || id === canonicalManualMergeSourceId.value) return null
      if (!canonicalEntitiesAreCrossConversationMergeCandidates(canonicalManualMergeSourceEntity.value, entity)) {
        return null
      }
      return {
        id,
        entity,
        label: canonicalEntityDisplayLabel(entity),
        meta: `${canonicalEntityConversationCount(entity)} 个来源会话；${canonicalEntitySourceLabelText(entity)}`,
      }
    })
    .filter(Boolean)
)
const canonicalManualMergeSelectedTarget = computed(() =>
  canonicalManualMergeTargetOptions.value.find(
    (item) => item.id === canonicalManualMergeTargetId.value
  ) || null
)
const selectedDossierCanonicalHasSingleSourceHint = computed(
  () =>
    !dossierCanonicalGraphLoading.value &&
    !dossierCanonicalGraphState.error &&
    selectedDossierCanonicalGraphConversationCount.value === 1 &&
    selectedDossierCanonicalGraphPreview.value.nodes.length > 0
)
const selectedDossierCanonicalIdentityTaskLookup = computed(() => {
  const lookup = {}
  for (const task of selectedDossierCanonicalTasks.value) {
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
const selectedDossierCanonicalPartyNamingTasks = computed(() =>
  selectedDossierIntelligenceConfirmationTasks.value.filter(
    (task) => String(task?.task_type || '').trim() === 'party_naming'
  )
)
const activePartyNamingRawLabels = computed(() => partyNamingTaskRawLabels(activePartyNamingTask.value))
const activePartyNamingSuggestedLabels = computed(() => partyNamingTaskSuggestedLabels(activePartyNamingTask.value))
const activePartyNamingCurrentLabel = computed(() => {
  const currentLabel = String(activePartyNamingTask.value?.current_label || '').trim()
  return currentLabel || activePartyNamingRawLabels.value[0] || '当事人'
})
const selectedDossierCanonicalGraphConversationCount = computed(() => {
  const statsCount = Number(selectedDossierCanonicalStats.value.boundConversations || 0)
  if (statsCount > 0) return statsCount
  const conversationIds = new Set()
  for (const entity of selectedDossierCanonicalEntities.value) {
    for (const conversationId of normalizeGraphConversationIds(entity?.source_conversation_ids)) {
      conversationIds.add(conversationId)
    }
  }
  if (conversationIds.size) return conversationIds.size
  return Number(selectedDossierConversationCount.value || 0)
})
const selectedDossierCanonicalGraphPreview = computed(() =>
  buildDossierCanonicalGraphPreview({
    entities: selectedDossierPrimaryCanonicalEntities.value,
    edges: Array.isArray(dossierCanonicalGraphState.edges) ? dossierCanonicalGraphState.edges : [],
    loading: dossierCanonicalGraphLoading.value,
    error: dossierCanonicalGraphState.error,
  })
)
const selectedDossierSelfAnchorIdentityTasks = computed(() =>
  selectedDossierIntelligenceConfirmationTasks.value.filter(
    (task) =>
      String(task?.task_type || '').trim() === 'entity_identity' &&
      String(task?.subject || '').trim() === 'self_anchor' &&
      !selfIdentityTaskIsDismissed(task)
  )
)
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
    dossierDrawerClosing.value = false
  }
})

watch(
  [
    currentConversationId,
    () => currentConversationDossierId(),
    () => selectedDossierCanonicalPartyNamingTasks.value.map((task) => dossierIntelligenceTaskKey(task)).join('|'),
  ],
  () => {
    if (
      partyNamingModalVisible.value &&
      activePartyNamingTask.value &&
      !partyNamingTaskSourceConversationIds(activePartyNamingTask.value).includes(Number(currentConversationId.value || 0))
    ) {
      partyNamingModalVisible.value = false
      activePartyNamingTask.value = null
      partyNamingInput.value = ''
      partyNamingError.value = ''
    }
    detectPendingPartyNamingForCurrentConversation()
  }
)

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

function resetDossierDetailForm() {
  dossierDetailForm.title = ''
  dossierDetailForm.case_type = ''
  dossierDetailForm.summary = ''
  dossierDetailForm.status = 'active'
}

function resetTrackedOnboardingForm(caseType = '') {
  const defaults = defaultParticipantDrafts(caseType)
  trackedOnboardingForm.title = defaults.title
  trackedOnboardingForm.primary_label = ''
  trackedOnboardingForm.primary_role = ''
  trackedOnboardingForm.opposing_label = ''
  trackedOnboardingForm.opposing_role = ''
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
    resetDossierIntelligenceState()
    resetDossierCanonicalGraphState()
    return
  }

  dossierDetailForm.title = dossier.title || ''
  dossierDetailForm.case_type = dossier.case_type || ''
  dossierDetailForm.summary = dossier.summary || ''
  dossierDetailForm.status = dossier.status || 'active'
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
  dossierCanonicalCrossTaskSubmittingKey.value = ''
  canonicalManualMergeDialogVisible.value = false
  canonicalManualMergeSourceEntity.value = null
  canonicalManualMergeTargetId.value = ''
  canonicalManualMergeSubmitting.value = false
  canonicalManualMergeError.value = ''
  partyNamingModalVisible.value = false
  activePartyNamingTask.value = null
  partyNamingInput.value = ''
  partyNamingSubmitting.value = false
  partyNamingError.value = ''
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
  detectPendingPartyNamingForCurrentConversation()
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

function partyNamingTaskEvidence(task) {
  return task?.evidence && typeof task.evidence === 'object' ? task.evidence : {}
}

function partyNamingTaskSourceConversationIds(task) {
  const evidence = partyNamingTaskEvidence(task)
  const values = [
    ...(Array.isArray(task?.source_conversation_ids) ? task.source_conversation_ids : []),
    ...(Array.isArray(evidence.source_conversation_ids) ? evidence.source_conversation_ids : []),
  ]
  return [...new Set(
    values
      .map((item) => Number(item || 0))
      .filter((item) => Number.isInteger(item) && item > 0)
  )]
}

function partyNamingTaskRawLabels(task) {
  const evidence = partyNamingTaskEvidence(task)
  const labels = [
    String(task?.current_label || '').trim(),
    ...(Array.isArray(task?.raw_labels) ? task.raw_labels : []),
    ...(Array.isArray(evidence.raw_labels) ? evidence.raw_labels : []),
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
  return labels.filter((label, index) => labels.indexOf(label) === index)
}

function partyNamingTaskSuggestedLabels(task) {
  const labels = [
    ...(Array.isArray(task?.suggested_names) ? task.suggested_names : []),
    ...(Array.isArray(task?.suggested_labels) ? task.suggested_labels : []),
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
  return labels.filter((label, index) => labels.indexOf(label) === index)
}

function isPendingSelfAnchorPartyNamingTask(task) {
  const taskType = String(task?.task_type || task?.type || '').trim()
  if (taskType !== 'party_naming') return false

  const status = String(task?.status || 'pending').trim() || 'pending'
  if (status !== 'pending') return false

  const subject = String(task?.subject || 'self_anchor').trim() || 'self_anchor'
  if (subject !== 'self_anchor') return false

  const labels = partyNamingTaskRawLabels(task)
  return labels.some((label) => PARTY_NAMING_SELF_ANCHOR_LABELS.has(label))
}

function currentConversationDossierId() {
  return Number(currentConversation.value?.dossier_id || 0)
}

function closePartyNamingModalState() {
  partyNamingModalVisible.value = false
  activePartyNamingTask.value = null
  partyNamingInput.value = ''
  partyNamingError.value = ''
}

function closeSelfIdentityModalState() {
  selfIdentityModalVisible.value = false
  activeSelfIdentityTask.value = null
}

function closeCanonicalManualMergeDialogState() {
  canonicalManualMergeDialogVisible.value = false
  if (!canonicalManualMergeSubmitting.value) {
    canonicalManualMergeSourceEntity.value = null
    canonicalManualMergeTargetId.value = ''
    canonicalManualMergeError.value = ''
  }
}

function activeSelfIdentityTasks() {
  return selectedDossierSelfAnchorIdentityTasks.value.filter((task) => !selfIdentityTaskIsSubmitting(task))
}

function handleCloseDossierDrawer(done) {
  if (dossierDrawerClosing.value) return
  dossierDrawerClosing.value = true
  closeCanonicalManualMergeDialogState()
  closePartyNamingModalState()
  closeSelfIdentityModalState()
  for (const task of activeSelfIdentityTasks()) {
    const state = ensureSelfIdentityTaskState(task)
    state.differentMode = false
    state.error = ''
  }
  done()
}

function findPendingPartyNamingTaskForCurrentConversation() {
  const conversationId = Number(currentConversationId.value || 0)
  const dossierId = currentConversationDossierId()
  if (conversationId <= 0 || dossierId <= 0) return null
  if (Number(dossierIntelligenceState.dossierId || 0) !== dossierId) return null

  return selectedDossierCanonicalPartyNamingTasks.value.find((task) => {
    if (!isPendingSelfAnchorPartyNamingTask(task)) return false
    if (dismissedPartyNamingTaskIds.value.has(dossierIntelligenceTaskKey(task))) return false
    return partyNamingTaskSourceConversationIds(task).includes(conversationId)
  }) || null
}

function detectPendingPartyNamingForCurrentConversation() {
  if (dossierDrawerClosing.value || partyNamingSubmitting.value || partyNamingModalVisible.value) return

  const task = findPendingPartyNamingTaskForCurrentConversation()
  if (!task) return

  const taskKey = dossierIntelligenceTaskKey(task)
  activePartyNamingTask.value = task
  const existingDraft = String(dossierPartyNamingDrafts[taskKey] || '').trim()
  const rawLabels = partyNamingTaskRawLabels(task)
  const suggestedLabels = partyNamingTaskSuggestedLabels(task)
  partyNamingInput.value =
    existingDraft &&
    !rawLabels.includes(existingDraft) &&
    !suggestedLabels.includes(existingDraft)
      ? existingDraft
      : ''
  partyNamingError.value = ''
  partyNamingModalVisible.value = true
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
  const rawLabels = partyNamingTaskRawLabels(task)
  const conversationIds = partyNamingTaskSourceConversationIds(task)
  const rawNodeIds = [
    ...(Array.isArray(task?.raw_node_ids) ? task.raw_node_ids : []),
    ...(Array.isArray(evidence.raw_node_ids) ? evidence.raw_node_ids : []),
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
  return {
    display_label: String(displayLabel || '').trim(),
    source_task: {
      subject: String(task?.subject || 'self_anchor').trim() || 'self_anchor',
      evidence: {
        raw_labels: rawLabels,
        source_conversation_ids: conversationIds,
        raw_node_ids: rawNodeIds.filter((item, index) => rawNodeIds.indexOf(item) === index),
      },
    },
  }
}

function resetDossierCanonicalGraphState() {
  dossierCanonicalGraphState.dossierId = null
  dossierCanonicalGraphState.entities = []
  dossierCanonicalGraphState.edges = []
  dossierCanonicalGraphState.tasks = []
  dossierCanonicalGraphState.dossierEntityState = null
  dossierCanonicalGraphState.stats = {
    boundConversations: 0,
    connectedEntities: 0,
    pendingIdentity: 0,
    pendingNaming: 0,
    isolatedAnchors: 0,
  }
  dossierCanonicalGraphState.view = 'canonical'
  dossierCanonicalGraphState.generatedAt = ''
  dossierCanonicalGraphState.error = ''
}

function applyDossierCanonicalGraphPayload(dossierId, payload) {
  const canonicalGraph = payload?.canonical_graph || payload?.coordination_graph || {}
  const stats = canonicalGraph?.stats || {}

  dossierCanonicalGraphState.dossierId = dossierId
  dossierCanonicalGraphState.entities = Array.isArray(canonicalGraph.entities)
    ? canonicalGraph.entities
    : []
  dossierCanonicalGraphState.edges = Array.isArray(canonicalGraph.edges)
    ? canonicalGraph.edges
    : []
  dossierCanonicalGraphState.tasks = Array.isArray(canonicalGraph.tasks)
    ? canonicalGraph.tasks
    : []
  dossierCanonicalGraphState.dossierEntityState =
    payload?.dossier_entity_state && typeof payload.dossier_entity_state === 'object'
      ? payload.dossier_entity_state
      : null
  dossierCanonicalGraphState.stats = {
    boundConversations: Number(stats.bound_conversations || stats.boundConversations || 0),
    connectedEntities: Number(stats.connected_entities || stats.connectedEntities || stats.canonical_entities || 0),
    pendingIdentity: Number(stats.pending_identity || stats.pendingIdentity || 0),
    pendingNaming: Number(stats.pending_naming || stats.pendingNaming || 0),
    isolatedAnchors: Number(stats.isolated_anchors || stats.isolatedAnchors || 0),
  }
  dossierCanonicalGraphState.view = String(payload?.view || 'canonical').trim() || 'canonical'
  dossierCanonicalGraphState.generatedAt = payload?.generated_at || ''
  dossierCanonicalGraphState.error = ''
}

function resetAnalysis() {
  currentAnalysis.actions = []
  currentAnalysis.sources = []
  currentAnalysis.routeIntent = ''
  currentAnalysis.latencyMs = null
  currentAnalysis.confidenceScore = null
  currentAnalysis.missingInfo = []
  currentAnalysis.executionTrace = null
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

function graphSnapshotSignature(graph = currentGraph) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : []
  const edges = Array.isArray(graph?.edges) ? graph.edges : []
  const nodeParts = nodes
    .map((node) => {
      const id = node?.id ?? ''
      const label = node?.label ?? node?.name ?? ''
      const type = node?.type ?? node?.category ?? ''
      return `${id}:${label}:${type}`
    })
    .sort()
  const edgeParts = edges
    .map((edge) => {
      const source = edge?.source ?? edge?.from ?? edge?.source_id ?? ''
      const target = edge?.target ?? edge?.to ?? edge?.target_id ?? ''
      const label = edge?.label ?? edge?.relation ?? edge?.type ?? ''
      return `${source}:${label}:${target}`
    })
    .sort()
  return JSON.stringify({ nodes: nodeParts, edges: edgeParts })
}

function clearCurrentConversationState() {
  currentConversationId.value = null
  currentSessionId.value = ''
  messages.value = []
  handledGraphClarificationKeys.value = new Set()
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
  currentAnalysis.executionTrace =
    data.execution_trace && typeof data.execution_trace === 'object'
      ? data.execution_trace
      : null
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
  graphRefreshInFlight.value = false
  if (options?.status) {
    setGraphSyncState(options.status, options.summary || '')
    if (options.status !== 'error' && options.refreshDossierAfterGraph !== false) {
      void refreshCurrentDossierAfterGraphSync()
    }
  }
}

async function refreshCurrentDossierAfterGraphSync() {
  const dossierId = currentConversationDossierId()
  if (dossierId <= 0) return

  selectedDossierId.value = dossierId
  await Promise.allSettled([
    loadSelectedDossierCanonicalGraph(dossierId, { silent: true }),
    loadSelectedDossierIntelligence(dossierId),
  ])
  detectPendingPartyNamingForCurrentConversation()
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
    executionTrace:
      metadata.execution_trace && typeof metadata.execution_trace === 'object'
        ? metadata.execution_trace
        : null,
    pendingClarification:
      metadata.pending_clarification && typeof metadata.pending_clarification === 'object'
        ? metadata.pending_clarification
        : null,
    clarificationQuestions: Array.isArray(metadata.clarification_questions)
      ? metadata.clarification_questions
      : [],
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
  currentAnalysis.executionTrace =
    latestAssistant.executionTrace && typeof latestAssistant.executionTrace === 'object'
      ? latestAssistant.executionTrace
      : null
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
    resetDossierCanonicalGraphState()
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
    resetDossierCanonicalGraphState()
    resetDossierIntelligenceState()
    if (!options.silent) {
      ElMessage.error('Failed to load dossier detail')
    }
  } finally {
    dossierDetailLoading.value = false
  }

  if (detailLoaded && options.loadCanonicalGraph !== false) {
    await loadSelectedDossierCanonicalGraph(dossierId, { silent: true })
  }
  if (detailLoaded && options.loadIntelligence !== false) {
    await loadSelectedDossierIntelligence(dossierId)
  }
}

async function loadSelectedDossierCanonicalGraph(dossierId, options = {}) {
  if (!dossierId) {
    resetDossierCanonicalGraphState()
    return
  }

  const isSameDossier = dossierCanonicalGraphState.dossierId === dossierId
  if (!isSameDossier) {
    resetDossierCanonicalGraphState()
    dossierCanonicalGraphState.dossierId = dossierId
  }

  dossierCanonicalGraphLoading.value = true
  dossierCanonicalGraphState.error = ''
  try {
    const { data } = await getDossierCanonicalGraph(dossierId)
    applyDossierCanonicalGraphPayload(dossierId, data)
  } catch (error) {
    console.error('Failed to fetch dossier canonical graph:', error)
    dossierCanonicalGraphState.dossierId = dossierId
    dossierCanonicalGraphState.error =
      typeof error?.response?.data?.detail === 'string' && error.response.data.detail
        ? error.response.data.detail
        : '案卷规范实体图暂不可用'
    if (!options.silent) {
      ElMessage.error(dossierCanonicalGraphState.error)
    }
  } finally {
    dossierCanonicalGraphLoading.value = false
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
    const scopedConversationId =
      Number(currentConversationDossierId() || 0) === Number(dossierId || 0)
        ? currentConversationId.value || undefined
        : undefined
    const { data } = await getDossierIntelligence(dossierId, {
      include_display_graph: true,
      conversation_id: scopedConversationId,
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

async function handleRefreshDossierCanonical() {
  if (!selectedDossierId.value) return
  await Promise.allSettled([
    loadSelectedDossierCanonicalGraph(selectedDossierId.value),
    loadSelectedDossierIntelligence(selectedDossierId.value),
  ])
}

async function refreshSelectedDossierAfterPartyNaming(dossierId) {
  await Promise.allSettled([
    loadSelectedDossierCanonicalGraph(dossierId, { silent: true }),
    loadSelectedDossierIntelligence(dossierId),
  ])

  const refreshErrors = [
    dossierCanonicalGraphState.error,
    dossierIntelligenceState.error,
  ].filter((value) => String(value || '').trim())

  if (refreshErrors.length) {
    console.error('Failed to refresh dossier views after party naming confirmation:', refreshErrors)
  }
  return refreshErrors.length === 0
}

async function refreshSelectedDossierAfterSelfIdentity(dossierId) {
  await Promise.allSettled([
    loadSelectedDossierCanonicalGraph(dossierId, { silent: true }),
    loadSelectedDossierIntelligence(dossierId),
  ])

  const refreshErrors = [
    dossierCanonicalGraphState.error,
    dossierIntelligenceState.error,
  ].filter((value) => String(value || '').trim())

  if (refreshErrors.length) {
    console.error('Failed to refresh dossier views after self identity confirmation:', refreshErrors)
  }
  return refreshErrors.length === 0
}

async function refreshSelectedDossierAfterCanonicalMerge(dossierId) {
  await Promise.allSettled([
    loadSelectedDossierCanonicalGraph(dossierId, { silent: true }),
    loadSelectedDossierIntelligence(dossierId),
  ])

  const refreshErrors = [
    dossierCanonicalGraphState.error,
    dossierIntelligenceState.error,
  ].filter((value) => String(value || '').trim())

  if (refreshErrors.length) {
    console.error('Failed to refresh dossier views after canonical merge:', refreshErrors)
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

function canonicalCrossConversationTaskKey(task) {
  return String(task?.task_key || task?.id || '').trim()
}

function canonicalCrossConversationTaskConfidenceLabel(task) {
  const confidence = String(task?.confidence || '').trim().toLowerCase()
  switch (confidence) {
    case 'high':
      return '高'
    case 'medium':
      return '中'
    case 'low':
      return '低'
    default:
      return '待评估'
  }
}

function canonicalCrossConversationTaskScoreText(task) {
  const score = Number(task?.score)
  if (!Number.isFinite(score) || score <= 0) return ''
  return `评分：${score.toFixed(2)}`
}

function isCanonicalCrossConversationTaskSubmitting(task) {
  return dossierCanonicalCrossTaskSubmittingKey.value === canonicalCrossConversationTaskKey(task)
}

function canonicalManualMergePayload(task) {
  const sourceEntity = task?.source_entity && typeof task.source_entity === 'object'
    ? task.source_entity
    : {}
  const targetEntity = task?.target_entity && typeof task.target_entity === 'object'
    ? task.target_entity
    : {}
  return {
    task_key: canonicalCrossConversationTaskKey(task) || null,
    reason: String(task?.reason || '').trim() || 'user_confirmed_same_entity',
    source_entity: {
      id: String(sourceEntity.id || '').trim(),
      label: String(sourceEntity.label || sourceEntity.canonical_label || '').trim(),
      entity_type: String(sourceEntity.entity_type || '').trim() || null,
      role: String(sourceEntity.role || '').trim() || null,
      source_entity_ids: Array.isArray(sourceEntity.source_entity_ids)
        ? sourceEntity.source_entity_ids.map((item) => String(item || '').trim()).filter(Boolean)
        : [],
      source_conversation_ids: normalizeGraphConversationIds(sourceEntity.source_conversation_ids),
    },
    target_entity: {
      id: String(targetEntity.id || '').trim(),
      label: String(targetEntity.label || targetEntity.canonical_label || '').trim(),
      entity_type: String(targetEntity.entity_type || '').trim() || null,
      role: String(targetEntity.role || '').trim() || null,
      source_entity_ids: Array.isArray(targetEntity.source_entity_ids)
        ? targetEntity.source_entity_ids.map((item) => String(item || '').trim()).filter(Boolean)
        : [],
      source_conversation_ids: normalizeGraphConversationIds(targetEntity.source_conversation_ids),
    },
  }
}

function canonicalManualMergeValidationError(task) {
  const payload = canonicalManualMergePayload(task)
  if (!payload.source_entity.id || !payload.target_entity.id) {
    return '缺少待合并实体标识'
  }
  if (!payload.source_entity.label || !payload.target_entity.label) {
    return '缺少待合并实体名称'
  }
  if (!payload.source_entity.source_entity_ids.length || !payload.target_entity.source_entity_ids.length) {
    return '缺少实体来源信息，暂时无法合并'
  }
  if (!payload.source_entity.source_conversation_ids.length || !payload.target_entity.source_conversation_ids.length) {
    return '缺少来源会话信息，暂时无法合并'
  }
  return ''
}

function canonicalManualMergeErrorMessage(error, fallback = '确认同一实体失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim()
  }
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') return item.msg || item.message || ''
        return ''
      })
      .filter(Boolean)
      .join('；') || fallback
  }
  return error?.message || fallback
}

function canonicalEntityDisplayLabel(entity) {
  const label = String(entity?.label || entity?.canonical_label || '').trim()
  return label || '未命名主体'
}

function canonicalEntityStableId(entity) {
  return String(entity?.id || entity?.entity_id || '').trim()
}

function canonicalEntitySourceEntityIds(entity) {
  return Array.isArray(entity?.source_entity_ids)
    ? entity.source_entity_ids.map((item) => String(item || '').trim()).filter(Boolean)
    : []
}

function canonicalEntitiesShareSourceConversation(left, right) {
  const leftIds = new Set(normalizeGraphConversationIds(left?.source_conversation_ids))
  if (!leftIds.size) return false
  return normalizeGraphConversationIds(right?.source_conversation_ids).some((conversationId) =>
    leftIds.has(conversationId)
  )
}

function canonicalEntitiesAreCrossConversationMergeCandidates(left, right) {
  if (!left || !right) return false
  if (!canonicalEntityStableId(left) || !canonicalEntityStableId(right)) return false
  const leftConversations = normalizeGraphConversationIds(left?.source_conversation_ids)
  const rightConversations = normalizeGraphConversationIds(right?.source_conversation_ids)
  if (!leftConversations.length || !rightConversations.length) return false
  return !canonicalEntitiesShareSourceConversation(left, right)
}

function canonicalEntityManualMergePayloadEntity(entity) {
  const id = canonicalEntityStableId(entity)
  return {
    id,
    entity_id: id,
    label: canonicalEntityDisplayLabel(entity),
    entity_type: String(entity?.entity_type || '').trim() || null,
    role: String(entity?.role || '').trim() || null,
    source_entity_ids: canonicalEntitySourceEntityIds(entity),
    source_conversation_ids: normalizeGraphConversationIds(entity?.source_conversation_ids),
  }
}

function canonicalManualMergeEntityValidationError(payloadEntity, label) {
  if (!payloadEntity.id) return `${label}缺少实体标识，暂时无法合并`
  if (!payloadEntity.label) return `${label}缺少实体名称，暂时无法合并`
  if (!payloadEntity.source_entity_ids.length) return `${label}缺少来源实体信息，暂时无法合并`
  if (!payloadEntity.source_conversation_ids.length) return `${label}缺少来源会话信息，暂时无法合并`
  return ''
}

function canonicalDecisionNodePayload(node, fallbackEntity = null) {
  const conversationId = Number(node?.conversation_id || 0)
  const graphNodeId = String(node?.graph_node_id || '').trim()
  if (conversationId <= 0 || !graphNodeId) return null

  const fallbackLabel = canonicalEntityDisplayLabel(fallbackEntity)
  const graphNodeLabel = String(node?.graph_node_label || node?.label || node?.raw_label || '').trim() || fallbackLabel
  const graphNodeType = String(
    node?.graph_node_type || node?.entity_type || fallbackEntity?.entity_type || ''
  ).trim() || null
  const rawLabel = String(node?.raw_label || node?.label || graphNodeLabel || fallbackLabel).trim() || fallbackLabel

  return {
    conversation_id: conversationId,
    graph_node_id: graphNodeId,
    raw_label: rawLabel,
    graph_node_label: graphNodeLabel || null,
    graph_node_type: graphNodeType,
  }
}

function canonicalEntityToCrossConversationNodes(entity) {
  const mentions = Array.isArray(entity?.mentions)
    ? entity.mentions.filter((item) => item && typeof item === 'object')
    : []

  const fromMentions = mentions
    .map((mention) => canonicalDecisionNodePayload(mention, entity))
    .filter(Boolean)

  if (fromMentions.length) {
    return fromMentions.filter((item, index, list) =>
      list.findIndex(
        (candidate) =>
          candidate.conversation_id === item.conversation_id &&
          candidate.graph_node_id === item.graph_node_id
      ) === index
    )
  }

  const conversationIds = normalizeGraphConversationIds(entity?.source_conversation_ids)
  const sourceEntityIds = Array.isArray(entity?.source_entity_ids)
    ? entity.source_entity_ids.map((item) => String(item || '').trim()).filter(Boolean)
    : []

  const fallbackNodes = []
  const count = Math.min(conversationIds.length, sourceEntityIds.length)
  for (let index = 0; index < count; index += 1) {
    const node = canonicalDecisionNodePayload({
      conversation_id: conversationIds[index],
      graph_node_id: sourceEntityIds[index],
      raw_label: canonicalEntityDisplayLabel(entity),
      graph_node_label: canonicalEntityDisplayLabel(entity),
      graph_node_type: String(entity?.entity_type || '').trim() || null,
    }, entity)
    if (node) fallbackNodes.push(node)
  }

  return fallbackNodes.filter((item, index, list) =>
    list.findIndex(
      (candidate) =>
        candidate.conversation_id === item.conversation_id &&
        candidate.graph_node_id === item.graph_node_id
    ) === index
  )
}

function canonicalRejectTaskNodePairs(task) {
  const subjectNode = canonicalDecisionNodePayload(task?.subject_node, task?.source_entity)
  const objectNode = canonicalDecisionNodePayload(task?.object_node, task?.target_entity)
  if (subjectNode && objectNode) {
    return [{ sourceNode: subjectNode, targetNode: objectNode }]
  }

  const sourceNodes = canonicalEntityToCrossConversationNodes(task?.source_entity)
  const targetNodes = canonicalEntityToCrossConversationNodes(task?.target_entity)
  const pairs = []

  for (const sourceNode of sourceNodes) {
    for (const targetNode of targetNodes) {
      if (
        sourceNode.conversation_id === targetNode.conversation_id &&
        sourceNode.graph_node_id === targetNode.graph_node_id
      ) {
        continue
      }
      pairs.push({ sourceNode, targetNode })
    }
  }

  return pairs
}

function canonicalRejectValidationError(task) {
  const sourceLabel = canonicalEntityDisplayLabel(task?.source_entity)
  const targetLabel = canonicalEntityDisplayLabel(task?.target_entity)
  if (!sourceLabel || !targetLabel) {
    return '缺少候选实体名称，暂时无法标记'
  }
  if (!canonicalRejectTaskNodePairs(task).length) {
    return '该候选缺少来源节点信息，暂时无法标记为不同实体'
  }
  return ''
}

function partyNamingErrorMessage(error, fallback = '确认主体名称失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') return item.msg || item.message || ''
        return ''
      })
      .filter(Boolean)
      .join('；') || fallback
  }
  return error?.message || fallback
}

async function submitPartyNamingTask(task, displayLabel, options = {}) {
  const dossierId = Number(dossierIntelligenceState.dossierId || selectedDossierId.value || currentConversationDossierId() || 0)
  if (dossierId <= 0) {
    const message = '当前案卷不可用'
    if (options.showInlineError) partyNamingError.value = message
    ElMessage.error(message)
    return false
  }

  const taskKey = dossierIntelligenceTaskKey(task)
  const normalizedLabel = String(displayLabel || '').trim()
  if (!normalizedLabel) {
    const message = '请输入该主体在本案中的名称或代称'
    if (options.showInlineError) partyNamingError.value = message
    ElMessage.warning(message)
    return false
  }

  dossierPartyNamingSubmittingKey.value = taskKey
  partyNamingSubmitting.value = true
  try {
    const { data } = await confirmDossierPartyNaming(
      dossierId,
      buildPartyNamingConfirmPayload(task, normalizedLabel),
    )

    if (data?.status && data.status !== 'success') {
      throw new Error(data.message || '确认主体名称失败')
    }

    if (options.dismissTaskOnSuccess !== false) {
      const nextDismissed = new Set(dismissedPartyNamingTaskIds.value)
      nextDismissed.add(taskKey)
      dismissedPartyNamingTaskIds.value = nextDismissed
    }

    dossierPartyNamingSubmittingKey.value = ''
    partyNamingSubmitting.value = false

    if (options.closeModalOnSuccess) {
      partyNamingModalVisible.value = false
      activePartyNamingTask.value = null
      partyNamingInput.value = ''
      partyNamingError.value = ''
    }

    const refreshed = await refreshSelectedDossierAfterPartyNaming(dossierId)
    if (refreshed) {
      ElMessage.success('已确认主体名称')
    } else {
      ElMessage.warning('已确认主体名称，部分视图刷新失败，请手动刷新')
    }
    return true
  } catch (error) {
    console.error('Failed to confirm dossier party naming:', error)
    const detail = partyNamingErrorMessage(error)
    if (options.showInlineError) partyNamingError.value = detail
    ElMessage.error(detail)
    return false
  } finally {
    dossierPartyNamingSubmittingKey.value = ''
    partyNamingSubmitting.value = false
  }
}

async function handleConfirmPartyNamingModal() {
  if (!activePartyNamingTask.value) return
  partyNamingError.value = ''
  await submitPartyNamingTask(activePartyNamingTask.value, partyNamingInput.value, {
    closeModalOnSuccess: true,
    showInlineError: true,
  })
}

function handleDismissPartyNamingModal() {
  if (activePartyNamingTask.value) {
    const nextDismissed = new Set(dismissedPartyNamingTaskIds.value)
    nextDismissed.add(dossierIntelligenceTaskKey(activePartyNamingTask.value))
    dismissedPartyNamingTaskIds.value = nextDismissed
  }
  partyNamingModalVisible.value = false
  activePartyNamingTask.value = null
  partyNamingInput.value = ''
  partyNamingError.value = ''
}

function handlePartyNamingModalClosed() {
  if (!partyNamingSubmitting.value && !partyNamingModalVisible.value) {
    activePartyNamingTask.value = null
    partyNamingInput.value = ''
    partyNamingError.value = ''
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

async function handleConfirmCrossConversationSameEntity(task) {
  const dossierId = Number(selectedDossierId.value || 0)
  if (dossierId <= 0) {
    ElMessage.error('当前案卷不可用')
    return
  }

  const validationError = canonicalManualMergeValidationError(task)
  if (validationError) {
    ElMessage.error(validationError)
    return
  }

  const payload = canonicalManualMergePayload(task)
  const sourceLabel = payload.source_entity.label || '来源实体'
  const targetLabel = payload.target_entity.label || '候选实体'
  const confidenceLabel = canonicalCrossConversationTaskConfidenceLabel(task)
  const scoreText = canonicalCrossConversationTaskScoreText(task)

  try {
    await ElMessageBox.confirm(
      `确认将“${sourceLabel}”与“${targetLabel}”标记为同一案卷实体？${scoreText ? `\n\n${scoreText}，置信度：${confidenceLabel}` : ''}`,
      '确认同一实体',
      {
        confirmButtonText: '确认合并',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }

  dossierCanonicalCrossTaskSubmittingKey.value = canonicalCrossConversationTaskKey(task)
  try {
    const { data } = await manualMergeDossierCanonicalEntities(dossierId, payload)
    if (data?.status && data.status !== 'success') {
      throw new Error(data.message || '确认同一实体失败')
    }

    const refreshed = await refreshSelectedDossierAfterCanonicalMerge(dossierId)
    if (refreshed) {
      ElMessage.success(`已确认“${sourceLabel}”与“${targetLabel}”为同一实体`)
    } else {
      ElMessage.warning(`已确认“${sourceLabel}”与“${targetLabel}”为同一实体，但部分视图刷新失败，请手动刷新`)
    }
  } catch (error) {
    console.error('Failed to confirm canonical cross-conversation same entity:', error)
    ElMessage.error(canonicalManualMergeErrorMessage(error))
  } finally {
    dossierCanonicalCrossTaskSubmittingKey.value = ''
  }
}

async function handleRejectCrossConversationSameEntity(task) {
  const dossierId = Number(selectedDossierId.value || currentConversationDossierId() || 0)
  if (dossierId <= 0) {
    ElMessage.error('当前案卷不可用')
    return
  }

  const validationError = canonicalRejectValidationError(task)
  if (validationError) {
    ElMessage.error(validationError)
    return
  }

  const taskKey = canonicalCrossConversationTaskKey(task)
  const sourceLabel = canonicalEntityDisplayLabel(task?.source_entity)
  const targetLabel = canonicalEntityDisplayLabel(task?.target_entity)
  const nodePairs = canonicalRejectTaskNodePairs(task)

  try {
    await ElMessageBox.confirm(
      `确认将“${sourceLabel}”与“${targetLabel}”标记为不是同一实体？`,
      '标记为不同实体',
      {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }

  dossierCanonicalCrossTaskSubmittingKey.value = taskKey
  try {
    await Promise.all(
      nodePairs.map(({ sourceNode, targetNode }) =>
        decideDossierCrossConversationResolution(dossierId, {
          subject: 'cross_conversation_identity',
          decision: 'not_same',
          source_node: sourceNode,
          target_node: targetNode,
          candidate_key: taskKey || null,
          task_key: taskKey || null,
          reason: 'user_marked_not_same',
        })
      )
    )

    const refreshed = await refreshSelectedDossierAfterCanonicalMerge(dossierId)
    if (refreshed) {
      ElMessage.success(`已将“${sourceLabel}”与“${targetLabel}”标记为不同实体`)
    } else {
      ElMessage.warning(`已将“${sourceLabel}”与“${targetLabel}”标记为不同实体，但部分视图刷新失败，请手动刷新`)
    }
  } catch (error) {
    console.error('Failed to reject canonical cross-conversation same entity:', error)
    ElMessage.error(canonicalManualMergeErrorMessage(error, '标记失败，请稍后重试'))
  } finally {
    dossierCanonicalCrossTaskSubmittingKey.value = ''
  }
}

function resetCanonicalManualMergeDialog() {
  if (canonicalManualMergeSubmitting.value) return
  canonicalManualMergeSourceEntity.value = null
  canonicalManualMergeTargetId.value = ''
  canonicalManualMergeError.value = ''
}

function handleOpenManualMerge(sourceEntity) {
  const dossierId = Number(selectedDossierId.value || 0)
  if (dossierId <= 0) {
    ElMessage.error('当前案卷不可用')
    return
  }

  const sourceEntityId = canonicalEntityStableId(sourceEntity)
  if (!sourceEntityId) {
    ElMessage.error('缺少来源实体标识，暂时无法合并')
    return
  }

  canonicalManualMergeSourceEntity.value = sourceEntity
  canonicalManualMergeTargetId.value = ''
  canonicalManualMergeError.value = ''
  if (!canonicalManualMergeTargetOptions.value.length) {
    canonicalManualMergeSourceEntity.value = null
    ElMessage.warning('当前没有可跨会话合并的其他实体')
    return
  }
  canonicalManualMergeDialogVisible.value = true
}

async function submitCanonicalManualMerge() {
  const dossierId = Number(selectedDossierId.value || 0)
  if (dossierId <= 0) {
    canonicalManualMergeError.value = '当前案卷不可用'
    return
  }

  const sourceEntity = canonicalManualMergeSourceEntity.value
  const targetEntity = canonicalManualMergeSelectedTarget.value?.entity
  if (!sourceEntity || !targetEntity) {
    canonicalManualMergeError.value = '请选择要合并的来源实体和目标实体'
    return
  }

  const sourcePayload = canonicalEntityManualMergePayloadEntity(sourceEntity)
  const targetPayload = canonicalEntityManualMergePayloadEntity(targetEntity)
  const validationError =
    canonicalManualMergeEntityValidationError(sourcePayload, '来源实体') ||
    canonicalManualMergeEntityValidationError(targetPayload, '目标实体')
  if (validationError) {
    canonicalManualMergeError.value = validationError
    return
  }

  if (sourcePayload.id === targetPayload.id) {
    canonicalManualMergeError.value = '来源实体和目标实体不能相同'
    return
  }

  const payload = {
    reason: 'user_manual_selected_target',
    source_entity: sourcePayload,
    target_entity: targetPayload,
  }

  canonicalManualMergeSubmitting.value = true
  canonicalManualMergeError.value = ''
  try {
    const { data } = await manualMergeDossierCanonicalEntities(dossierId, payload)
    if (data?.status && data.status !== 'success') {
      throw new Error(data.message || '手动合并失败')
    }

    const refreshed = await refreshSelectedDossierAfterCanonicalMerge(dossierId)
    canonicalManualMergeDialogVisible.value = false
    if (refreshed) {
      ElMessage.success(`已将“${sourcePayload.label}”合并到“${targetPayload.label}”`)
    } else {
      ElMessage.warning(`已将“${sourcePayload.label}”合并到“${targetPayload.label}”，但部分视图刷新失败，请手动刷新`)
    }
  } catch (error) {
    console.error('Failed to manual merge canonical entities:', error)
    canonicalManualMergeError.value = canonicalManualMergeErrorMessage(error, '手动合并失败')
    ElMessage.error(canonicalManualMergeError.value)
  } finally {
    canonicalManualMergeSubmitting.value = false
  }
}

function canonicalEntityConversationCount(entity) {
  return normalizeGraphConversationIds(entity?.source_conversation_ids).length
}

function canonicalEntitySourceLabels(entity) {
  const labels = Array.isArray(entity?.source_labels) ? entity.source_labels : []
  return labels.map((item) => String(item || '').trim()).filter(Boolean)
}

function canonicalEntitySourceLabelText(entity) {
  const labels = canonicalEntitySourceLabels(entity)
  return labels.length ? labels.join(' / ') : '暂无来源称呼'
}

function canonicalEntitySourceConversations(entity) {
  const items = Array.isArray(entity?.source_conversations) ? entity.source_conversations : []
  return items
    .map((item) => {
      const conversationId = Number(item?.conversation_id || 0)
      return {
        conversation_id: conversationId,
        title: String(item?.title || '').trim() || formatGraphConversationLabel(conversationId),
        source_labels: Array.isArray(item?.source_labels)
          ? item.source_labels.map((label) => String(label || '').trim()).filter(Boolean)
          : [],
        fact_count: Number(item?.fact_count || 0),
        relation_count: Number(item?.relation_count || 0),
        is_best: Boolean(item?.is_best),
      }
    })
    .filter((item) => item.conversation_id > 0)
}

function canonicalEntitySourceConversationBadges(entity) {
  const sourceConversations = canonicalEntitySourceConversations(entity)
  const bestConversationId = canonicalEntityDetailConversationId(entity)
  const fromSourceConversations = sourceConversations.map((item) => ({
    id: item.conversation_id,
    label: item.title || formatGraphConversationLabel(item.conversation_id),
    isBest: item.is_best || item.conversation_id === bestConversationId,
  }))

  if (fromSourceConversations.length) return fromSourceConversations

  return normalizeGraphConversationIds(entity?.source_conversation_ids).map((conversationId) => ({
    id: conversationId,
    label: formatGraphConversationLabel(conversationId),
    isBest: conversationId === bestConversationId,
  }))
}

function canonicalEntityStatusLabel(status) {
  switch (String(status || '').trim()) {
    case 'canonical':
      return '案卷实体'
    case 'confirmed':
      return '已连接主体'
    case 'pending_identity':
      return '待确认连接'
    case 'pending_naming':
      return '待命名主体'
    case 'isolated_anchor':
      return '单会话主体'
    case 'generic_label':
      return '泛化称呼'
    case 'unconfirmed_label':
      return '未确认称呼'
    default:
      return '其他节点'
  }
}

function canonicalEntityRawNodeIds(entity) {
  return Array.isArray(entity?.raw_node_ids)
    ? entity.raw_node_ids.map((item) => String(item || '').trim()).filter(Boolean)
    : []
}

function canonicalEntityDetailConversationId(entity) {
  const bestConversationId = Number(entity?.best_conversation_id || 0)
  if (bestConversationId > 0) return bestConversationId
  return normalizeGraphConversationIds(entity?.source_conversation_ids)[0] || 0
}

function canonicalEntityDetailConversationLabel(entity) {
  const conversationId = canonicalEntityDetailConversationId(entity)
  return conversationId > 0 ? formatGraphConversationLabel(conversationId) : '暂无可跳转会话'
}

function canonicalEntityIdentityReason(entity) {
  const task = selectedDossierCanonicalIdentityTaskLookup.value[String(entity?.id || '').trim()]
  return String(task?.reason || '').trim()
}

function canonicalEntityHasDetailConversation(entity) {
  return canonicalEntityDetailConversationId(entity) > 0
}

function canonicalTaskEntitySummary(entity) {
  if (!entity || typeof entity !== 'object') return '未标注'
  const label = String(entity.label || entity.canonical_label || '').trim() || '未命名实体'
  const role = String(entity.role || '').trim()
  const entityType = formatGraphNodeType(entity.entity_type)
  const aliases = Array.isArray(entity.aliases)
    ? entity.aliases.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  const suffix = [
    role ? `角色：${role}` : '',
    entityType ? `类型：${entityType}` : '',
    aliases.length ? `别名：${aliases.join(' / ')}` : '',
  ].filter(Boolean)
  return suffix.length ? `${label}（${suffix.join('，')}）` : label
}

function canonicalTaskPairTitle(task) {
  const sourceLabel =
    String(task?.source_entity?.label || task?.source_entity?.canonical_label || '').trim() ||
    '来源实体'
  const targetLabel =
    String(task?.target_entity?.label || task?.target_entity?.canonical_label || '').trim() ||
    '候选实体'
  return `${sourceLabel} 和 ${targetLabel} 是否为同一案卷实体？`
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

async function handleOpenCanonicalEntityConversation(entity) {
  const conversationId = canonicalEntityDetailConversationId(entity)
  if (conversationId <= 0) {
    ElMessage.info('暂无可跳转会话')
    return
  }
  await switchConversation(conversationId)
}

async function handleOpenCanonicalGraphNodeConversation(node) {
  const conversationId = Number(node?.actionConversationId || 0)
  if (conversationId <= 0) {
    ElMessage.info('暂无可跳转会话')
    return
  }
  await switchConversation(conversationId)
}

const canonicalDrawerHelpers = {
  canonicalEntityConversationCount,
  canonicalEntityDetailConversationLabel,
  canonicalEntityHasDetailConversation,
  canonicalEntitySourceLabelText,
  canonicalEntitySourceConversationBadges,
  canonicalEntityIdentityReason,
  canonicalTaskPairTitle,
  canonicalTaskEntitySummary,
  canonicalTaskConfidenceLabel: canonicalCrossConversationTaskConfidenceLabel,
  canonicalTaskScoreText: canonicalCrossConversationTaskScoreText,
  canonicalTaskIsSubmitting: isCanonicalCrossConversationTaskSubmitting,
  formatGraphConversationLabel,
  formatGraphConversationListLabel,
  selfAnchorIdentityTaskPrompt,
  selfIdentityTaskState,
  selfIdentityTaskIsDifferentMode,
  selfIdentityTaskError,
  selfIdentityTaskIsSubmitting,
  selfIdentityTaskIsSubmittingAction,
  dossierIntelligenceTaskKey,
  isPartyNamingSubmitting,
  canSubmitPartyNaming,
  formatDossierIntelligenceEvidenceList,
}

function graphNodeRawNodeIds(node) {
  if (!node || typeof node !== 'object') return []
  const rawNodeIds = [
    ...(Array.isArray(node.raw_node_ids) ? node.raw_node_ids : []),
    node.properties?.raw_node_id,
  ]
    .map((item) => String(item || '').trim())
    .filter(Boolean)

  const nodeId = String(node.id || '').trim()
  if (nodeId.toLowerCase().startsWith('raw:')) {
    rawNodeIds.push(nodeId)
  }

  return rawNodeIds.filter((value, index) => rawNodeIds.indexOf(value) === index)
}

function isConversationGraphNode(node) {
  const nodeId = String(node?.id || '').trim().toLowerCase()
  const nodeType = String(node?.entity_type || node?.type || node?.graph_node_type || '').trim().toLowerCase()
  return nodeId.startsWith('conversation:') || nodeType === 'conversation'
}

function canonicalSupplementCategory(node) {
  const nodeType = String(node?.entity_type || node?.type || node?.graph_node_type || '').trim().toLowerCase()
  switch (nodeType) {
    case 'event':
    case 'fact':
      return { category: 'event', label: nodeType === 'fact' ? '事实节点' : '事件节点' }
    case 'evidence':
      return { category: 'evidence', label: '证据节点' }
    case 'document':
      return { category: 'document', label: '文书节点' }
    case 'amount':
    case 'money':
      return { category: 'amount', label: '金额节点' }
    case 'time':
    case 'date':
      return { category: 'time', label: '时间节点' }
    default:
      return { category: 'other', label: formatGraphNodeType(nodeType) }
  }
}

function canonicalSupplementStatusLabel(node) {
  const { label } = canonicalSupplementCategory(node)
  return `关联${label}`
}

function buildCanonicalEntityGraphNode(entity) {
  const sourceConversationIds = normalizeGraphConversationIds(entity?.source_conversation_ids)
  const sourceLabels = canonicalEntitySourceLabels(entity)
  const sourceConversations = canonicalEntitySourceConversations(entity)
  const rawNodeIds = canonicalEntityRawNodeIds(entity)
  const participantId = Number(entity?.participant_id || 0)
  const detailConversationId = canonicalEntityDetailConversationId(entity)
  const status = String(entity?.status || '').trim()
  const aliases = Array.isArray(entity?.aliases)
    ? entity.aliases.map((item) => String(item || '').trim()).filter(Boolean)
    : sourceLabels.filter((item) => item !== String(entity?.label || '').trim())
  const reason =
    canonicalEntityIdentityReason(entity) ||
    String(entity?.best_conversation_reason || '').trim() ||
    (status === 'isolated_anchor' ? '该会话暂未与其他会话形成确认连接' : '')

  return {
    id: String(entity?.id || '').trim(),
    label: String(entity?.label || '').trim() || sourceLabels[0] || '未命名主体',
    category: status || 'other',
    categoryLabel: canonicalEntityStatusLabel(status),
    labels: sourceLabels,
    sourceConversations,
    properties: {
      entity_type: entity?.entity_type,
      participant_id: participantId || undefined,
      best_conversation_id: detailConversationId || undefined,
    },
    detailRows: [
      { key: '类型', value: formatGraphNodeType(entity?.entity_type) },
      { key: '状态', value: canonicalEntityStatusLabel(status) },
      {
        key: '来源会话',
        value: sourceConversationIds.length
          ? formatGraphConversationListLabel(sourceConversationIds)
          : '会话未标注',
      },
      ...(aliases.length ? [{ key: '别名', value: aliases.join(' / ') }] : []),
      ...(sourceLabels.length ? [{ key: '原始标签', value: sourceLabels.join(' / ') }] : []),
      ...(detailConversationId > 0
        ? [{ key: '最佳会话', value: formatGraphConversationLabel(detailConversationId) }]
        : []),
      ...(reason ? [{ key: '说明', value: reason }] : []),
    ],
    debugRows: [
      ...(rawNodeIds.length ? [{ key: 'raw_node_ids', value: rawNodeIds.join(' / ') }] : []),
      ...(participantId > 0 ? [{ key: 'participant_id', value: String(participantId) }] : []),
    ],
    actionLabel: detailConversationId > 0 ? '查看信息最多的会话' : '',
    actionDisabled: detailConversationId <= 0,
    actionConversationId: detailConversationId,
  }
}

function buildCanonicalSupplementGraphNode(node) {
  const sourceConversationIds = graphNodeConversationIds(node)
  const rawLabels = graphNodeRawLabels(node)
  const rawNodeIds = graphNodeRawNodeIds(node)
  const participantId = Number(node?.participant_id || node?.properties?.participant_id || 0)
  const detailConversationId = sourceConversationIds[0] || 0
  const category = canonicalSupplementCategory(node)

  return {
    id: `display:${String(node?.id || '').trim()}`,
    label: graphNodePrimaryLabel(node),
    category: category.category,
    categoryLabel: category.label,
    labels: rawLabels,
    properties: node?.properties && typeof node.properties === 'object' ? node.properties : {},
    detailRows: [
      { key: '类型', value: formatGraphNodeType(node?.entity_type || node?.type || node?.graph_node_type) },
      { key: '状态', value: canonicalSupplementStatusLabel(node) },
      {
        key: '来源会话',
        value: sourceConversationIds.length
          ? formatGraphConversationListLabel(sourceConversationIds)
          : '会话未标注',
      },
      ...(rawLabels.length ? [{ key: '原始标签', value: rawLabels.join(' / ') }] : []),
      ...(rawNodeIds.length ? [{ key: 'raw_node_ids', value: rawNodeIds.join(' / ') }] : []),
      ...(participantId > 0 ? [{ key: 'participant_id', value: String(participantId) }] : []),
      ...(detailConversationId > 0
        ? [{ key: '详细对话', value: formatGraphConversationLabel(detailConversationId) }]
        : []),
    ],
    actionLabel: detailConversationId > 0 ? '查看详细对话' : '',
    actionDisabled: detailConversationId <= 0,
    actionConversationId: detailConversationId,
  }
}

function matchCanonicalEntityIdForDisplayNode(node, participantLookup, rawNodeLookup) {
  if (!node || typeof node !== 'object' || isConversationGraphNode(node)) return ''
  const participantId = Number(node?.participant_id || node?.properties?.participant_id || 0)
  if (participantId > 0 && participantLookup.has(participantId)) {
    return participantLookup.get(participantId)
  }
  for (const rawNodeId of graphNodeRawNodeIds(node)) {
    if (rawNodeLookup.has(rawNodeId)) {
      return rawNodeLookup.get(rawNodeId)
    }
  }
  return ''
}

function buildCanonicalGraphLegendItems(nodes) {
  const baseItems = [
    { category: 'canonical', label: '案卷实体', color: '#2563eb' },
    { category: 'confirmed', label: '已连接主体', color: '#2563eb' },
    { category: 'pending_identity', label: '待确认主体', color: '#f59e0b' },
    { category: 'pending_naming', label: '待命名主体', color: '#7c3aed' },
    { category: 'isolated_anchor', label: '单会话主体', color: '#64748b' },
    { category: 'generic_label', label: '泛化称呼', color: '#94a3b8' },
    { category: 'unconfirmed_label', label: '未确认称呼', color: '#94a3b8' },
    { category: 'other', label: '其他节点', color: '#475569' },
  ]
  const usedCategories = new Set(
    nodes.map((node) => String(node?.category || '').trim()).filter(Boolean)
  )
  return baseItems.filter((item) => usedCategories.has(item.category))
}

function normalizeCanonicalEdgeEvidence(edge) {
  return Array.isArray(edge?.evidence)
    ? edge.evidence.filter((item) => item && typeof item === 'object')
    : []
}

function buildCanonicalRelationGraphEdge(edge) {
  const evidence = normalizeCanonicalEdgeEvidence(edge)
  const edgeConversationIds = [
    ...normalizeGraphConversationIds(edge?.source_conversation_ids),
    ...evidence.flatMap((item) => normalizeGraphConversationIds(item?.conversation_id)),
  ].filter((value, index, array) => array.indexOf(value) === index)

  return {
    id: String(
      edge?.id ||
        `${String(edge?.source || '').trim()}-${String(edge?.target || '').trim()}-${
          edge?.label || edge?.relation_type || 'relation'
        }`
    ),
    source: String(edge?.source || '').trim(),
    target: String(edge?.target || '').trim(),
    label: String(edge?.label || edge?.relation_type || '关联').trim() || '关联',
    properties: {
      relation_type: String(edge?.relation_type || '').trim(),
      confidence: String(edge?.confidence || '').trim(),
    },
    conversationIds: edgeConversationIds,
    evidence,
  }
}

function appendCanonicalRelationDetails(graphNodes, graphEdges) {
  const incidentEdgeLookup = new Map()
  for (const node of graphNodes) {
    incidentEdgeLookup.set(node.id, [])
  }

  for (const edge of graphEdges) {
    if (incidentEdgeLookup.has(edge.source)) {
      incidentEdgeLookup.get(edge.source).push(edge)
    }
    if (incidentEdgeLookup.has(edge.target)) {
      incidentEdgeLookup.get(edge.target).push(edge)
    }
  }

  for (const node of graphNodes) {
    const relatedEdges = incidentEdgeLookup.get(node.id) || []
    if (!relatedEdges.length) continue

    const relationLabels = []
    const evidenceSummaries = []
    const evidenceConversationIds = []

    for (const edge of relatedEdges) {
      const label = String(edge?.label || '').trim()
      if (label && !relationLabels.includes(label)) {
        relationLabels.push(label)
      }
      for (const evidence of normalizeCanonicalEdgeEvidence(edge)) {
        const summary = String(
          evidence?.summary || evidence?.reason || evidence?.message_text || evidence?.raw_edge_id || ''
        ).trim()
        if (summary && !evidenceSummaries.includes(summary)) {
          evidenceSummaries.push(summary)
        }
        for (const conversationId of normalizeGraphConversationIds(evidence?.conversation_id)) {
          if (!evidenceConversationIds.includes(conversationId)) {
            evidenceConversationIds.push(conversationId)
          }
        }
      }
    }

    const detailRows = Array.isArray(node.detailRows) ? [...node.detailRows] : []
    if (relationLabels.length) {
      detailRows.push({ key: '关系线索', value: relationLabels.join(' / ') })
    }
    if (evidenceSummaries.length) {
      detailRows.push({ key: '关系证据', value: evidenceSummaries.slice(0, 2).join('；') })
    }
    node.detailRows = detailRows

    if (evidenceSummaries.length > 2) {
      node.detailNote = `已汇总 ${evidenceSummaries.length} 条关系证据，可切换到详细对话继续查看。`
    }

    const actionConversationId = Number(node.actionConversationId || 0)
    if (actionConversationId <= 0 && evidenceConversationIds.length) {
      node.actionConversationId = evidenceConversationIds[0]
      node.actionLabel = '查看详细对话'
      node.actionDisabled = false
      node.detailRows.push({
        key: '详细对话',
        value: formatGraphConversationLabel(evidenceConversationIds[0]),
      })
    }
  }
}

function buildDossierCanonicalGraphPreview(payload = {}) {
  const entities = Array.isArray(payload.entities) ? payload.entities : []
  const canonicalEdges = Array.isArray(payload.edges) ? payload.edges : []
  const graphNodes = []
  const graphEdges = []
  const nodeLookup = new Map()

  for (const entity of entities) {
    const graphNode = buildCanonicalEntityGraphNode(entity)
    if (!graphNode.id) continue
    graphNodes.push(graphNode)
    nodeLookup.set(graphNode.id, graphNode)
  }

  const edgeKeys = new Set()
  for (const edge of canonicalEdges) {
    if (graphEdges.length >= DOSSIER_COORDINATION_GRAPH_EDGE_LIMIT) break
    const graphEdge = buildCanonicalRelationGraphEdge(edge)
    if (
      !graphEdge.source ||
      !graphEdge.target ||
      !nodeLookup.has(graphEdge.source) ||
      !nodeLookup.has(graphEdge.target) ||
      graphEdge.source === graphEdge.target
    ) {
      continue
    }
    const edgeKey = [graphEdge.source, graphEdge.target, graphEdge.label].join('::')
    if (edgeKeys.has(edgeKey)) continue
    edgeKeys.add(edgeKey)
    graphEdges.push(graphEdge)
  }

  appendCanonicalRelationDetails(graphNodes, graphEdges)

  return {
    metaLabel: '案卷规范实体图',
    query:
      '这里只展示案卷中的责任主体和关键主体关系，例如个人、公司、单位、机构及其代表关系。劳动合同、社保、金额、通知等事实信息会作为详情证据展示，不作为主节点。',
    nodes: graphNodes,
    edges: graphEdges,
    loading: Boolean(payload.loading),
    error: String(payload.error || '').trim(),
    loadingText: '正在加载案卷规范实体图...',
    emptyText:
      '暂无案卷规范实体图。绑定并生成多个会话图谱后，将在这里展示可连接主体。',
    ariaLabel: '案卷规范实体图',
    legendItems: buildCanonicalGraphLegendItems(graphNodes),
  }
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
  } catch (error) {
    console.error('Failed to attach conversation to dossier:', error)
    ElMessage.error('绑定案卷失败')
  } finally {
    dossierBindingId.value = null
  }
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
    await refreshDossiers({ silent: true })
    if (dossierStatusFilter.value === 'active' && selectedDossierId.value === dossier.id) {
      selectedDossierId.value = filteredDossiers.value[0]?.id || null
    }
    if (selectedDossierId.value) {
      await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    } else {
      applySelectedDossierDetail(null)
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
    await refreshConversationList(currentConversationId.value)
    await refreshDossiers({ silent: true })
    if (deletingSelectedDossier) {
      selectedDossierId.value = null
      applySelectedDossierDetail(null)
    } else if (selectedDossierId.value) {
      await loadSelectedDossierDetail(selectedDossierId.value, { silent: true })
    } else {
      applySelectedDossierDetail(null)
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

  if (!title) {
    ElMessage.warning('请先填写案卷名称')
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

    const participantAliases = buildParticipantAliasesPayload(trackedOnboardingForm)
    if (Object.keys(participantAliases).length > 0) {
      const { data: participantsData } = await updateDossierParticipants(dossierId, {
        participant_aliases: participantAliases,
      })
      if (participantsData?.dossier) {
        upsertDossierLocal(participantsData.dossier)
      }
    }

    if (conversation?.dossier_id !== dossierId) {
      const { data: attachData } = await attachConversationToDossier(conversationId, dossierId)
      if (attachData?.conversation) {
        upsertConversationLocal(attachData.conversation)
      }
    } else {
      const { data: consentData } = await setConversationModeConsent(conversationId, {
        mode: 'TRACKED_CASE',
        consent: 'tracked',
        reason: 'user_selected_tracked_dossier',
      })
      if (consentData?.conversation) {
        upsertConversationLocal(consentData.conversation)
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
    return false
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
    if (requestId !== latestGraphRequestId.value) return false
    currentGraph.query = data.query || query || currentGraph.query
    currentGraph.nodes = data.nodes || []
    currentGraph.edges = data.edges || []
    currentGraph.tokens = data.tokens || []
    currentGraph.generatedAt = data.generated_at || ''
    return true
  } catch (error) {
    if (requestId !== latestGraphRequestId.value) return false
    currentGraph.error = error?.response?.data?.detail || '图谱加载失败。'
    return false
  } finally {
    if (requestId === latestGraphRequestId.value && !options.silent) {
      currentGraph.loading = false
    }
  }
}

function graphClarificationKey(message) {
  const pending = message?.pendingClarification
  if (!pending || typeof pending !== 'object') return ''
  const messageId = message?.id || message?.key || ''
  const mention = String(pending.target_mention || '').trim()
  const originalText = String(pending.pending_graph_update_input_text || '').trim()
  return `${messageId}:${mention}:${originalText}`
}

function normalizeGraphClarificationText(value) {
  return String(value || '').trim().replace(/\s+/g, ' ')
}

function messageNumericId(message) {
  const value = Number(message?.id)
  return Number.isFinite(value) ? value : 0
}

function maxPersistedMessageId() {
  return messages.value.reduce((maxId, message) => {
    const messageId = messageNumericId(message)
    return messageId > maxId ? messageId : maxId
  }, 0)
}

function responsePlansGraphUpdate(data = {}) {
  const plan = data?.execution_plan && typeof data.execution_plan === 'object'
    ? data.execution_plan
    : null
  if (plan?.graph_update_allowed === true) return true
  const steps = Array.isArray(plan?.steps) ? plan.steps : []
  return steps.some((step) => String(step?.name || '').trim() === 'graph_update')
}

function responseGraphUpdateInputText(data = {}, fallbackText = '') {
  const resolvedInputs = data?.execution_plan?.resolved_inputs
  const graphInput = resolvedInputs && typeof resolvedInputs === 'object'
    ? resolvedInputs.graph_update_input_text
    : ''
  return normalizeGraphClarificationText(graphInput || fallbackText)
}

function findLatestGraphClarificationMessage(options = {}) {
  const afterMessageId = Number(options.afterMessageId || 0)
  const expectedInputText = normalizeGraphClarificationText(options.expectedInputText)
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    const message = messages.value[index]
    if (afterMessageId > 0 && messageNumericId(message) <= afterMessageId) continue
    if (message.role !== 'assistant') continue
    const pendingClarification = message.pendingClarification
    if (expectedInputText) {
      const pendingInputText = normalizeGraphClarificationText(
        pendingClarification?.pending_graph_update_input_text
      )
      if (pendingInputText !== expectedInputText) continue
    }
    const key = graphClarificationKey(message)
    if (key && !handledGraphClarificationKeys.value.has(key)) {
      return { message, key }
    }
  }
  return null
}

async function refreshMessagesForGraphClarification(conversationId, options = {}) {
  if (!conversationId) return null
  try {
    const { data } = await getConversationMessages(conversationId)
    const backendMessages = Array.isArray(data?.messages) ? data.messages : []
    messages.value = backendMessages.map((message, index) => buildMessageViewModel(message, index))
    syncCurrentAnalysisFromMessages()
    scrollBottom()
    return findLatestGraphClarificationMessage(options)
  } catch (error) {
    console.error('Failed to refresh messages for graph clarification:', error)
    return null
  }
}

async function promptGraphClarificationIfNeeded(conversationId, options = {}) {
  if (!conversationId || graphClarificationPromptOpen.value) return false
  const pending = findLatestGraphClarificationMessage(options)
    || (await refreshMessagesForGraphClarification(conversationId, options))
  if (!pending) return false

  const { message, key } = pending
  const clarification = message.pendingClarification || {}
  const question =
    String(message.clarificationQuestions?.[0] || clarification.previous_question || '').trim()
    || '图谱落位时发现仍有泛称主体，请确认它具体指哪个责任主体。'

  handledGraphClarificationKeys.value.add(key)
  graphClarificationPromptOpen.value = true
  try {
    const result = await ElMessageBox.prompt(question, '确认图谱主体', {
      confirmButtonText: '确认并写入',
      cancelButtonText: '稍后处理',
      inputPlaceholder: '例如：张三、李四、蓝海科技',
      inputValidator: (value) => {
        const normalized = String(value || '').trim()
        if (!normalized) return '请输入明确主体名称'
        if (normalized.length > 40) return '主体名称过长，请直接输入名称'
        return true
      },
      distinguishCancelAndClose: true,
    })
    const value = String(result?.value || '').trim()
    if (!value) return false
    stopGraphRefreshPolling({
      status: 'warning',
      summary: '图谱落位需要主体确认，已提交确认并重新触发写入',
      refreshDossierAfterGraph: false,
    })
    await sendImpl(value, {
      conversationId,
      modeConsentEnabled: false,
      restoreDraftOnErrorText: value,
    })
    return true
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      console.error('Graph clarification prompt failed:', error)
    }
    return false
  } finally {
    graphClarificationPromptOpen.value = false
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

function startGraphRefreshPolling(conversationId, previousSignature = '', query = '', options = {}) {
  stopGraphRefreshPolling()
  if (!conversationId) return
  const initialSignature = previousSignature || graphSnapshotSignature()
  const clarificationOptions = {
    afterMessageId: Number(options.afterMessageId || 0),
    expectedInputText: normalizeGraphClarificationText(options.expectedInputText || query),
  }
  setGraphSyncState('running', '图谱更新任务已提交，正在等待后台写入完成')

  graphRefreshTimer.value = setInterval(async () => {
    if (graphRefreshInFlight.value) return
    graphRefreshInFlight.value = true
    try {
      const handledClarification = await promptGraphClarificationIfNeeded(
        conversationId,
        clarificationOptions
      )
      if (handledClarification) {
        return
      }

      const applied = await fetchConversationGraph(conversationId, query, { preserveData: true, silent: true })
      const nextSignature = graphSnapshotSignature()
      if (applied && nextSignature !== initialSignature) {
        stopGraphRefreshPolling({
          status: 'success',
          summary: '图谱同步已完成，本轮图谱变更已刷新到侧栏',
        })
      }
    } catch (error) {
      console.error('Graph refresh polling failed:', error)
      stopGraphRefreshPolling({
        status: 'warning',
        summary: '图谱同步检查失败，请稍后手动刷新图谱',
      })
    } finally {
      graphRefreshInFlight.value = false
    }
  }, 5000)

  graphRefreshStopTimer.value = setTimeout(() => {
    stopGraphRefreshPolling({
      status: 'warning',
      summary: '图谱后台任务耗时较长，仍可能继续写入；请稍后切换会话或刷新侧栏查看',
    })
  }, 90000)
}

function updatePendingMessage(tempId, patch = {}) {
  const target = messages.value.find((item) => item.id === tempId)
  if (!target) return
  Object.assign(target, patch)
}

function removeMessageById(messageId) {
  messages.value = messages.value.filter((item) => item.id !== messageId)
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

function buildParticipantAliasesPayload(form) {
  const primaryLabel = String(form.primary_label || '').trim()
  const primaryRole = String(form.primary_role || '').trim()
  const opposingLabel = String(form.opposing_label || '').trim()
  const opposingRole = String(form.opposing_role || '').trim()
  const payload = {}
  if (primaryLabel && primaryRole) {
    payload.primary_party = {
      label: String(form.primary_label || '').trim(),
      role: String(form.primary_role || '').trim(),
      party_side: 'claimant',
    }
  }
  if (opposingLabel && opposingRole) {
    payload.opposing_party = {
      label: String(form.opposing_label || '').trim(),
      role: String(form.opposing_role || '').trim(),
      party_side: 'opponent',
    }
  }
  return payload
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
  const participantAliases = boundDossier?.participant_aliases || {}
  const primary = participantAliases?.primary_party || {}
  const opposing = participantAliases?.opposing_party || {}
  trackedOnboardingForm.primary_label = primary.label || ''
  trackedOnboardingForm.primary_role = primary.role || ''
  trackedOnboardingForm.opposing_label = opposing.label || ''
  trackedOnboardingForm.opposing_role = opposing.role || ''
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
    prompt || '我注意到你正在描述一个具体案情。你可以仅在本轮分析，或将它纳入案卷持续跟踪。',
    '',
    '仅本轮分析不会把这次案情纳入案卷持续跟踪；进入案卷持续跟踪后，系统会将当前会话绑定到案卷并持续维护相关案情上下文。',
  ].join('\n')

  try {
    await ElMessageBox.confirm(
      message,
      '模式确认',
      {
        confirmButtonText: '进入案卷持续跟踪',
        cancelButtonText: '仅本轮分析',
        distinguishCancelAndClose: true,
        type: 'warning',
      }
    )
    return 'tracked_dossier'
  } catch (error) {
    if (error === 'cancel') {
      return 'temporary_analysis'
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
  const previousGraphSignature = graphSnapshotSignature()

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

      if (nextAction === 'tracked_dossier') {
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

      const payload = {
        mode: 'CASE_ANALYSIS',
        consent: 'temporary',
        reason: 'user_selected_temporary_analysis',
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
    const beforeResponseMessageId = maxPersistedMessageId()
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
      executionTrace:
        data.execution_trace && typeof data.execution_trace === 'object'
          ? data.execution_trace
          : null,
      pending: false,
    })

    applyCurrentAnalysisResponse(data)
    const shouldWatchGraphUpdate = responsePlansGraphUpdate(data)
    setGraphSyncState(
      shouldWatchGraphUpdate ? 'running' : 'idle',
      shouldWatchGraphUpdate ? '主请求已完成，正在检查图谱更新任务' : ''
    )
    void ensureConversationTitle(text)
    await refreshConversationList(baseConversationId)
    await fetchConversationGraph(baseConversationId, text)
    if (shouldWatchGraphUpdate) {
      startGraphRefreshPolling(baseConversationId, previousGraphSignature, text, {
        afterMessageId: beforeResponseMessageId,
        expectedInputText: responseGraphUpdateInputText(data, text),
      })
    } else {
      stopGraphRefreshPolling({ refreshDossierAfterGraph: false })
    }
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
      executionTrace: null,
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

.dossier-auto-summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dossier-inline-actions-spread {
  justify-content: space-between;
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

.canonical-merge-dialog {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.canonical-merge-source,
.canonical-merge-preview {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
}

.canonical-merge-source span,
.canonical-merge-preview span {
  font-size: 12px;
  color: #6b7280;
}

.canonical-merge-source strong,
.canonical-merge-preview strong {
  font-size: 14px;
  color: #111827;
}

.canonical-merge-source small,
.canonical-merge-preview small {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}

.canonical-merge-target-select {
  width: 100%;
}

.canonical-merge-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.3;
}

.canonical-merge-option strong {
  font-size: 13px;
  color: #111827;
}

.canonical-merge-option span {
  font-size: 12px;
  color: #6b7280;
}

.party-naming-modal {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.party-naming-title {
  font-size: 16px;
  font-weight: 700;
  color: #111827;
}

.party-naming-subtitle {
  font-size: 12px;
  line-height: 1.6;
  color: #6b7280;
}

.party-naming-meta {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  font-size: 12px;
  color: #475569;
}

.party-naming-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.party-naming-error {
  margin-top: 2px;
}

.party-naming-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
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
  .dossier-intelligence-grid,
  .tracked-onboarding-grid {
    grid-template-columns: 1fr;
  }

  .dossier-item-main,
  .dossier-detail-header,
  .dossier-auto-summary-header {
    flex-direction: column;
    align-items: stretch;
  }

  .dossier-canonical-entity-row {
    flex-direction: column;
  }

  .dossier-canonical-entity-actions {
    width: 100%;
    justify-content: flex-start;
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
