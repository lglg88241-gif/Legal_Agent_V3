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
                  type="danger"
                  :disabled="dossier.status === 'archived'"
                  @click.stop="handleArchiveDossier(dossier)"
                >
                  {{ dossier.status === 'archived' ? '已归档' : '归档' }}
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
              type="danger"
              plain
              :disabled="selectedDossierDetail?.status === 'archived'"
              @click="handleArchiveDossier(selectedDossierDetail)"
            >
              {{ selectedDossierDetail?.status === 'archived' ? '已归档' : '归档案卷' }}
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

          <div class="dossier-participants-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-toolbar-copy">
                <div class="dossier-detail-conversations-title">主体与关系</div>
                <span class="dossier-toolbar-subtitle">
                  “主体”指案卷中的人、公司或机构；“确认主体”就是确认不同说法是否指同一个人或公司。
                </span>
              </div>
              <div class="dossier-inline-actions">
                <el-button size="small" plain @click="openParticipantCandidateDialog">
                  处理建议
                </el-button>
                <el-button size="small" plain @click="toggleDossierParticipantSection('create')">
                  手动新建主体
                </el-button>
              </div>
            </div>
            <div class="dossier-participants-hint">
              系统只会提出主体候选，不会自动合并；确认后，案卷图谱和案卷记忆会更稳定。
            </div>
            <div class="dossier-participants-hint">
              当事人称呼与主体确认都只在当前案卷内生效，不会自动与其他案卷的同名主体合并。
            </div>
            <div class="dossier-participant-summary-grid">
              <div class="dossier-participant-summary-card">
                <span>已确认主体</span>
                <strong>{{ selectedDossierResolvedParticipants.length }}</strong>
              </div>
              <div class="dossier-participant-summary-card">
                <span>待确认建议</span>
                <strong>{{ selectedDossierPendingParticipantCount }}</strong>
              </div>
              <div class="dossier-participant-summary-card">
                <span>已确认映射</span>
                <strong>{{ selectedDossierParticipantMentions.length }}</strong>
              </div>
            </div>
            <div
              v-if="selectedDossierPendingParticipantCount"
              class="dossier-participant-smart-tip"
            >
              {{ `有 ${selectedDossierPendingParticipantCount} 个主体关系待确认，确认后图谱会更清晰。` }}
            </div>
            <div
              v-if="selectedDossierResolvedParticipants.length"
              class="dossier-participant-pill-list"
            >
              <div
                v-for="participant in selectedDossierResolvedParticipants"
                :key="participant.id"
                class="dossier-participant-pill"
              >
                <strong>{{ participant.label || `主体 #${participant.id}` }}</strong>
                <span>{{ participant.role || '未分类角色' }}</span>
                <span>
                  {{ `${participant.party_side || 'unknown'} · ${participant.entity_type || 'unknown'}` }}
                </span>
                <span>
                  {{ `已确认 mentions ${selectedDossierParticipantMentionCountMap[participant.id] || 0}` }}
                </span>
                <el-button size="small" text @click="ensureDossierParticipantSection('participants')">
                  编辑
                </el-button>
              </div>
            </div>
            <div v-else class="dossier-graph-empty">
              暂无已确认主体。你可以先处理待确认建议，也可以手动新建主体。
            </div>
          </div>

          <div class="dossier-resolved-participants-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-detail-conversations-title">主体确认详情</div>
              <el-button
                size="small"
                plain
                :loading="dossierParticipantRegistryLoading || dossierParticipantCandidatesLoading"
                @click="loadSelectedDossierDetail(selectedDossierId, { silent: true })"
              >
                刷新主体
              </el-button>
            </div>

            <div v-if="dossierParticipantRegistryState.error" class="dossier-graph-error">
              {{ dossierParticipantRegistryState.error }}
            </div>
            <div v-else-if="dossierParticipantRegistryLoading" class="dossier-list-status">
              正在加载主体确认...
            </div>
            <div v-else class="dossier-resolved-participants-body">
              <el-collapse v-model="dossierParticipantSections" class="dossier-participant-collapse">
                <el-collapse-item name="aliases">
                  <template #title>
                    <div class="dossier-collapse-title">
                      <strong>案卷称呼锚点</strong>
                      <span>固定“我 / 对方 / 公司”等常用称呼，便于持续跟踪。</span>
                    </div>
                  </template>
                  <div class="dossier-inline-actions dossier-inline-actions-spread">
                    <div class="dossier-participants-hint">
                      这些称呼是用户确认的案卷别名，可使用化名或角色名，不等同于真实姓名。
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
                      暂无已确认主体，可先从待确认建议中处理，或手动新建一个主体。
                    </div>
                  </div>
                </el-collapse-item>

                <el-collapse-item name="create">
                  <template #title>
                    <div class="dossier-collapse-title">
                      <strong>手动新建主体</strong>
                      <span>适合提前登记案卷中的人、公司或机构，不要求真实姓名。</span>
                    </div>
                  </template>
                  <div class="dossier-participant-create-card">
                    <div class="dossier-resolved-participant-grid">
                      <el-input
                        v-model="dossierParticipantCreateForm.label"
                        maxlength="100"
                        placeholder="主体称呼，例如：A公司 / 张三"
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

                <el-collapse-item name="candidates">
                  <template #title>
                    <div class="dossier-collapse-title">
                      <strong>待确认建议</strong>
                      <span>{{ `共 ${selectedDossierParticipantCandidates.length} 项；系统只建议，不会自动合并。` }}</span>
                    </div>
                  </template>
                  <div v-if="dossierParticipantCandidatesLoading" class="dossier-list-status">
                    正在生成候选主体...
                  </div>
                  <div
                    v-else-if="selectedDossierParticipantCandidates.length"
                    class="dossier-participant-candidate-list"
                  >
                    <div
                      v-for="candidate in selectedDossierParticipantCandidates"
                      :key="candidate.candidate_key"
                      class="dossier-participant-candidate-card"
                    >
                      <div class="dossier-participant-candidate-title">
                        {{ candidate.raw_labels?.join(' / ') || candidate.suggested_label }}
                      </div>
                      <div class="dossier-participant-candidate-meta">
                        {{
                          `建议称呼：${candidate.suggested_label || '未命名主体'} · 角色：${candidate.suggested_role || '未分类'} · mentions：${candidate.mentions?.length || 0}`
                        }}
                      </div>
                      <div v-if="candidate.existing_participant_label" class="dossier-participant-candidate-meta">
                        {{ `系统建议可映射到：${candidate.existing_participant_label}` }}
                      </div>
                      <el-select
                        v-model="dossierParticipantCandidateSelections[candidate.candidate_key]"
                        clearable
                        placeholder="选择已有主体"
                      >
                        <el-option
                          v-for="participant in selectedDossierResolvedParticipants"
                          :key="participant.id"
                          :label="participant.label || `主体 #${participant.id}`"
                          :value="participant.id"
                        />
                      </el-select>
                      <div class="dossier-participant-candidate-actions">
                        <el-button
                          size="small"
                          type="primary"
                          plain
                          :disabled="!dossierParticipantCandidateSelections[candidate.candidate_key]"
                          :loading="dossierParticipantActionLoading"
                          @click="handleResolveParticipantCandidate(candidate, { participantId: dossierParticipantCandidateSelections[candidate.candidate_key] })"
                        >
                          确认为已有主体
                        </el-button>
                        <el-button
                          size="small"
                          type="primary"
                          :loading="dossierParticipantActionLoading"
                          @click="handleResolveParticipantCandidate(candidate)"
                        >
                          新建为独立主体
                        </el-button>
                        <el-button
                          size="small"
                          @click="handleIgnoreParticipantCandidate(candidate)"
                        >
                          暂不处理
                        </el-button>
                      </div>
                    </div>
                  </div>
                  <div v-else class="dossier-graph-empty">
                    暂无待确认主体候选。若后续绑定更多会话或图谱新增主体称呼，可刷新后再看。
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
          </div>

          <div class="dossier-graph-section">
            <div class="dossier-auto-summary-header">
              <div class="dossier-detail-conversations-title">图谱状态</div>
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
            <div class="dossier-graph-hint">
              当前展示的是案卷下已绑定会话的聚合图谱统计，只读查看，不会改动图谱写入。
            </div>
            <div class="dossier-graph-hint">
              当前图谱为案卷局部图谱；节点和关系来自绑定会话中的结构化案情，可能包含未确认推断。
            </div>
            <div class="dossier-graph-hint">
              如绑定了不同案情会话，图谱可能出现混杂，建议结合案卷摘要和原始会话继续确认。
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
                图谱服务暂不可用，当前显示为空状态。
              </div>
              <div v-if="selectedDossierPendingParticipantCount" class="dossier-graph-warning dossier-graph-warning-soft">
                {{ `有 ${selectedDossierPendingParticipantCount} 个主体关系待确认，确认后图谱会更清晰。` }}
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
                  案卷图谱由该案卷绑定会话中的结构化案情信息聚合生成，可能包含系统推断的未确认关系。
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
                        <strong>{{ node.display_label || node.label || `Node ${node.id}` }}</strong>
                        <span v-if="node.raw_label && node.raw_label !== (node.display_label || node.label)">
                          {{ `原始称呼：${node.raw_label}` }}
                        </span>
                        <span>{{ node.type || '未分类' }}</span>
                        <span>
                          {{
                            formatGraphConversationLabel(
                              node.conversation_id ?? node.properties?.conversation_id
                            )
                          }}
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
                          {{ formatGraphConversationLabel(edge.conversationId) }}
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
    title="确认主体建议"
    width="560px"
  >
    <div class="dossier-participants-hint">
      新会话或新图谱中出现了可能重复的称呼。请确认哪些说法指向同一个人、公司或机构；系统不会自动合并。
    </div>
    <div
      v-if="selectedDossierParticipantCandidates.length"
      class="dossier-participant-candidate-list"
    >
      <div
        v-for="candidate in selectedDossierParticipantCandidates"
        :key="candidate.candidate_key"
        class="dossier-participant-candidate-card"
      >
        <div class="dossier-participant-candidate-title">
          {{ candidate.raw_labels?.join(' / ') || candidate.suggested_label }}
        </div>
        <div class="dossier-participant-candidate-meta">
          {{
            `建议称呼：${candidate.suggested_label || '未命名主体'} · 角色：${candidate.suggested_role || '未分类'} · mentions：${candidate.mentions?.length || 0}`
          }}
        </div>
        <div v-if="candidate.existing_participant_label" class="dossier-participant-candidate-meta">
          {{ `系统建议可映射到：${candidate.existing_participant_label}` }}
        </div>
        <el-select
          v-model="dossierParticipantCandidateSelections[candidate.candidate_key]"
          clearable
          placeholder="选择已有主体"
        >
          <el-option
            v-for="participant in selectedDossierResolvedParticipants"
            :key="participant.id"
            :label="participant.label || `主体 #${participant.id}`"
            :value="participant.id"
          />
        </el-select>
        <div class="dossier-participant-candidate-actions">
          <el-button
            size="small"
            type="primary"
            plain
            :disabled="!dossierParticipantCandidateSelections[candidate.candidate_key]"
            :loading="dossierParticipantActionLoading"
            @click="handleResolveParticipantCandidate(candidate, { participantId: dossierParticipantCandidateSelections[candidate.candidate_key] })"
          >
            确认为已有主体
          </el-button>
          <el-button
            size="small"
            type="primary"
            :loading="dossierParticipantActionLoading"
            @click="handleResolveParticipantCandidate(candidate)"
          >
            新建为独立主体
          </el-button>
          <el-button size="small" @click="handleIgnoreParticipantCandidate(candidate)">
            暂不处理
          </el-button>
        </div>
      </div>
    </div>
    <div v-else class="dossier-graph-empty">
      当前没有待确认的主体建议。
    </div>

    <template #footer>
      <el-button @click="dossierParticipantCandidateDialogVisible = false">
        我稍后处理
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
  createConversation,
  createDossier,
  createDossierParticipant,
  deleteConversation,
  generateDossierSummary,
  getConversationGraph,
  getConversationMessages,
  getConversationSummary,
  getConversations,
  getDossierDetail,
  getDossierGraph,
  getDossierParticipantCandidates,
  getDossierParticipants,
  getDossiers,
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
const dossierParticipantSections = ref([])
const dossierParticipantCandidateDialogVisible = ref(false)
const dossierSummaryGenerating = ref(false)
const dossierGraphLoading = ref(false)
const dossierGraphLoaded = ref(false)
const trackedOnboardingVisible = ref(false)
const trackedOnboardingSubmitting = ref(false)
const dossierGraphState = reactive({
  dossierId: null,
  conversationCount: 0,
  nodeCount: 0,
  edgeCount: 0,
  nodes: [],
  edges: [],
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

const currentAnalysis = reactive({ actions: [], sources: [] })
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
const selectedDossierParticipantCandidates = computed(() =>
  Array.isArray(dossierParticipantRegistryState.candidates)
    ? dossierParticipantRegistryState.candidates.filter(
        (candidate) => !dossierParticipantCandidateDismissed[candidate.candidate_key]
      )
    : []
)
const selectedDossierPendingParticipantCount = computed(
  () => selectedDossierParticipantCandidates.value.length
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
      sourceLabel: sourceNode?.display_label || sourceNode?.label || sourceId || 'Unknown',
      targetLabel: targetNode?.display_label || targetNode?.label || targetId || 'Unknown',
      relationLabel: String(edge?.label || edge?.type || 'CASE_RELATION').trim() || 'CASE_RELATION',
      conversationId:
        properties.conversation_id ??
        sourceNode?.conversation_id ??
        sourceNode?.properties?.conversation_id ??
        targetNode?.conversation_id ??
        targetNode?.properties?.conversation_id ??
        null,
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
    return '图谱服务暂不可用，当前显示为空状态。'
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
    ElMessage.info('当前没有待确认的主体建议')
    return
  }
  ensureDossierParticipantSection('candidates')
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
  dossierParticipantSections.value = []
  dossierParticipantCandidateDialogVisible.value = false
  resetDossierParticipantCreateForm()
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
  Object.keys(dossierParticipantCandidateSelections).forEach((key) => {
    delete dossierParticipantCandidateSelections[key]
  })
  for (const candidate of dossierParticipantRegistryState.candidates) {
    const candidateKey = candidate?.candidate_key
    if (!candidateKey) continue
    dossierParticipantCandidateSelections[candidateKey] =
      candidate?.existing_participant_id || null
  }
}

function resetDossierGraphState() {
  dossierGraphState.dossierId = null
  dossierGraphState.conversationCount = 0
  dossierGraphState.nodeCount = 0
  dossierGraphState.edgeCount = 0
  dossierGraphState.nodes = []
  dossierGraphState.edges = []
  dossierGraphState.generatedAt = ''
  dossierGraphState.warning = ''
  dossierGraphState.error = ''
  dossierGraphState.participantAliases = {}
  dossierGraphLoaded.value = false
}

function applyDossierGraphPayload(dossierId, payload) {
  const stats = payload?.graph?.stats || {}
  const graph = payload?.graph || {}
  const participantAliases = payload?.participant_aliases
  dossierGraphState.dossierId = dossierId
  dossierGraphState.conversationCount = Number(stats.conversation_count || 0)
  dossierGraphState.nodeCount = Number(stats.node_count || 0)
  dossierGraphState.edgeCount = Number(stats.edge_count || 0)
  dossierGraphState.nodes = Array.isArray(graph.nodes) ? graph.nodes : []
  dossierGraphState.edges = Array.isArray(graph.edges) ? graph.edges : []
  dossierGraphState.generatedAt = payload?.generated_at || ''
  dossierGraphState.warning = typeof payload?.warning === 'string' ? payload.warning : ''
  dossierGraphState.error = ''
  dossierGraphState.participantAliases =
    participantAliases && typeof participantAliases === 'object'
      ? participantAliases
      : selectedDossierDetail.value?.participant_aliases || {}
  dossierGraphLoaded.value = true
}

function resetAnalysis() {
  currentAnalysis.actions = []
  currentAnalysis.sources = []
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

function stopGraphRefreshPolling() {
  if (graphRefreshTimer.value) {
    clearInterval(graphRefreshTimer.value)
    graphRefreshTimer.value = null
  }
  if (graphRefreshStopTimer.value) {
    clearTimeout(graphRefreshStopTimer.value)
    graphRefreshStopTimer.value = null
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
    sources: metadata.sources || [],
    agentActions: metadata.agent_actions || [],
    pending: false,
  }
}

function syncCurrentAnalysisFromMessages() {
  resetAnalysis()
  const latestAssistant = [...messages.value].reverse().find((item) => item.role !== 'user')
  if (!latestAssistant) return
  currentAnalysis.actions = latestAssistant.agentActions || []
  currentAnalysis.sources = latestAssistant.sources || []
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
  try {
    const { data } = await getDossierGraph(dossierId)
    applyDossierGraphPayload(dossierId, data)
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

async function handleRefreshDossierGraph() {
  if (!selectedDossierId.value) return
  await loadSelectedDossierGraph(selectedDossierId.value)
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

  ensureDossierParticipantSection('candidates')
  dossierParticipantCandidateDialogVisible.value = true

  const firstCandidate = selectedDossierParticipantCandidates.value[0]
  const rawLabels = firstCandidate?.raw_labels?.join(' / ') || firstCandidate?.suggested_label || '新主体'
  const existingLabel = String(firstCandidate?.existing_participant_label || '').trim()
  const tip =
    pendingCount === 1
      ? existingLabel
        ? `新会话中出现“${rawLabels}”，可能与案卷中的“${existingLabel}”是同一主体。请确认是否合并。`
        : `新会话中出现“${rawLabels}”这个新称呼。请确认它是否属于已有主体，或新建为独立主体。`
      : `新会话中出现 ${pendingCount} 个待确认主体称呼。请确认哪些指向同一主体，哪些应保留为独立主体。`

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
  })
  await loadSelectedDossierParticipants(selectedDossierId.value, { silent: true })
  await loadSelectedDossierParticipantCandidates(selectedDossierId.value, { silent: true })
  await loadSelectedDossierGraph(selectedDossierId.value, { silent: true })
  if (!selectedDossierParticipantCandidates.value.length) {
    dossierParticipantCandidateDialogVisible.value = false
  }
  if (!options.silent) {
    ElMessage.success('主体确认已更新')
  }
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
  if (!selectedDossierId.value || !candidate?.candidate_key) return

  const participantId = options.participantId || dossierParticipantCandidateSelections[candidate.candidate_key]
  const mentions = Array.isArray(candidate.mentions) ? candidate.mentions : []
  if (!mentions.length) {
    ElMessage.warning('当前候选没有可确认的主体称呼')
    return
  }

  const payload = {
    mentions: mentions.map((mention) => ({
      conversation_id: mention.conversation_id,
      raw_label: mention.raw_label,
      source: mention.source || 'graph',
      confidence: typeof mention.confidence === 'number' ? mention.confidence : null,
    })),
  }

  if (participantId) {
    payload.participant_id = participantId
  } else {
    payload.participant = {
      label: candidate.suggested_label || candidate.raw_labels?.[0] || '未命名主体',
      role: candidate.suggested_role || null,
      party_side: candidate.party_side || 'unknown',
      entity_type: candidate.entity_type || 'unknown',
      description: null,
    }
  }

  dossierParticipantActionLoading.value = true
  try {
    await resolveDossierParticipants(selectedDossierId.value, payload)
    delete dossierParticipantCandidateDismissed[candidate.candidate_key]
    await refreshSelectedDossierParticipantViews({ silent: true })
    ElMessage.success(participantId ? '候选称呼已映射到已确认主体' : '候选称呼已新建并确认主体')
  } catch (error) {
    console.error('Failed to resolve dossier participant candidate:', error)
    const detail = error?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' && detail ? detail : '确认主体失败')
  } finally {
    dossierParticipantActionLoading.value = false
  }
}

function handleIgnoreParticipantCandidate(candidate) {
  const candidateKey = candidate?.candidate_key
  if (!candidateKey) return
  dossierParticipantCandidateDismissed[candidateKey] = true
  nextTick(() => {
    if (!selectedDossierParticipantCandidates.value.length) {
      dossierParticipantCandidateDialogVisible.value = false
    }
  })
  ElMessage.info('已忽略该候选，可刷新后重新查看')
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

  graphRefreshTimer.value = setInterval(async () => {
    try {
      await fetchConversationGraph(conversationId, query, { preserveData: true, silent: true })
      if ((currentGraph.nodes?.length || 0) !== previousNodeCount) {
        stopGraphRefreshPolling()
      }
    } catch (error) {
      console.error('Graph refresh polling failed:', error)
      stopGraphRefreshPolling()
    }
  }, 3000)

  graphRefreshStopTimer.value = setTimeout(() => {
    stopGraphRefreshPolling()
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

function formatGraphPropertiesTitle(properties) {
  if (!properties || typeof properties !== 'object') return ''

  return Object.entries(properties)
    .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
    .slice(0, 6)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join('\n')
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

    messages.value.push({
      id: `assistant-${Date.now()}`,
      key: `assistant-${Date.now()}`,
      role: 'assistant',
      content: data.response || '未返回有效内容。',
      time: formatTime(new Date().toISOString()),
      sources: data.sources || [],
      agentActions: data.agent_actions || [],
      pending: false,
    })

    currentAnalysis.actions = data.agent_actions || []
    currentAnalysis.sources = data.sources || []
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
    messages.value.push({
      id: `assistant-error-${Date.now()}`,
      key: `assistant-error-${Date.now()}`,
      role: 'assistant',
      content: `请求失败：${error.message || '未知错误'}`,
      time: formatTime(new Date().toISOString()),
      sources: [],
      agentActions: [],
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
  .dossier-graph-aliases,
  .dossier-participant-summary-grid,
  .dossier-participants-grid,
  .dossier-resolved-participant-grid,
  .tracked-onboarding-grid {
    grid-template-columns: 1fr;
  }

  .dossier-item-main,
  .dossier-detail-header,
  .dossier-auto-summary-header {
    flex-direction: column;
    align-items: stretch;
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
