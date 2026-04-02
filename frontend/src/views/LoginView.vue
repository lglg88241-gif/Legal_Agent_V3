<template>
  <div class="auth-page">
    <div class="auth-shell">
      <div class="auth-copy">
        <p class="eyebrow">Legal Agent V3</p>
        <h1 class="headline">登录后即可进入法律智能工作台</h1>
        <p class="subhead">
          登录后，系统会自动携带 JWT Token，保护你的会话、检索结果和多轮追问数据隔离。
        </p>
      </div>

      <div class="auth-card">
        <div class="auth-tabs">
          <button
            :class="['tab-button', { active: mode === 'login' }]"
            @click="mode = 'login'"
          >
            登录
          </button>
          <button
            :class="['tab-button', { active: mode === 'register' }]"
            @click="mode = 'register'"
          >
            注册
          </button>
        </div>

        <el-form label-position="top" @submit.prevent="handleSubmit">
          <el-form-item label="用户名">
            <el-input v-model="form.username" placeholder="请输入用户名" autocomplete="username" />
          </el-form-item>

          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="请输入密码"
              autocomplete="current-password"
            />
          </el-form-item>

          <el-button
            class="submit-button"
            type="primary"
            :loading="submitting"
            @click="handleSubmit"
          >
            {{ mode === 'login' ? '登录并进入工作台' : '注册并创建账号' }}
          </el-button>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { login, register } from '../api/index.js'
import { setAuthSession } from '../utils/auth.js'

const router = useRouter()
const route = useRoute()

const mode = ref('login')
const submitting = ref(false)
const form = reactive({
  username: '',
  password: '',
})

async function handleSubmit() {
  const username = form.username.trim()
  const password = form.password.trim()

  if (!username || !password) {
    ElMessage.warning('请输入用户名和密码。')
    return
  }

  if (username.length < 2) {
    ElMessage.warning('用户名至少需要 2 个字符。')
    return
  }

  if (password.length < 6) {
    ElMessage.warning('密码至少需要 6 个字符。')
    return
  }

  submitting.value = true
  try {
    const action = mode.value === 'login' ? login : register
    const { data } = await action(username, password)
    setAuthSession(data)
    ElMessage.success(mode.value === 'login' ? '登录成功。' : '注册成功。')
    router.replace(route.query.redirect || '/chat')
  } catch (error) {
    const detail = error?.response?.data?.detail
    const message = Array.isArray(detail)
      ? detail.map((item) => item?.msg || '请求校验失败').join('；')
      : detail ||
        (mode.value === 'login' ? '登录失败，请检查用户名或密码。' : '注册失败，请稍后重试。')
    ElMessage.error(message)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background:
    radial-gradient(circle at top left, rgba(14, 165, 233, 0.18), transparent 30%),
    radial-gradient(circle at bottom right, rgba(251, 191, 36, 0.18), transparent 28%),
    linear-gradient(135deg, #f8fafc 0%, #e0f2fe 48%, #fff7ed 100%);
}

.auth-shell {
  width: min(1120px, 100%);
  display: grid;
  grid-template-columns: 1.1fr 0.9fr;
  gap: 28px;
  align-items: stretch;
}

.auth-copy {
  padding: 44px;
  border-radius: 28px;
  background: rgba(15, 23, 42, 0.9);
  color: #f8fafc;
  box-shadow: 0 28px 80px rgba(15, 23, 42, 0.28);
}

.eyebrow {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: #7dd3fc;
}

.headline {
  margin: 18px 0 12px;
  font-size: clamp(32px, 4vw, 54px);
  line-height: 1.05;
  font-weight: 800;
}

.subhead {
  margin: 0;
  max-width: 32rem;
  line-height: 1.7;
  color: rgba(248, 250, 252, 0.84);
}

.auth-card {
  padding: 32px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(10px);
}

.auth-tabs {
  display: inline-flex;
  gap: 6px;
  margin-bottom: 24px;
  padding: 6px;
  border-radius: 999px;
  background: #e2e8f0;
}

.tab-button {
  border: none;
  background: transparent;
  color: #475569;
  font-weight: 700;
  padding: 10px 18px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.tab-button.active {
  background: #0f172a;
  color: #f8fafc;
}

.submit-button {
  width: 100%;
  margin-top: 10px;
  height: 46px;
}

@media (max-width: 900px) {
  .auth-shell {
    grid-template-columns: 1fr;
  }

  .auth-copy,
  .auth-card {
    padding: 28px;
  }
}
</style>
