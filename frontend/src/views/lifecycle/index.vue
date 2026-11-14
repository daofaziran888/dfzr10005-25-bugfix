<template>
  <section class="page" data-module="lifecycle">
    <header class="page-head">
      <div>
        <h2>表生命周期管理管理</h2>
        <p class="page-desc">维护数仓表的生命周期策略定义、归档路径与清理方式，为表生命周期管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记生命周期策略</button>
        <button class="btn" type="button" @click="exportRows">导出表生命周期管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无表生命周期管理数据，可先登记生命周期策略</td>
        </tr>
      </tbody>
    </table>

    <header class="page-head" style="margin-top: 18px;">
      <div>
        <h3>生命周期结果清单（清理资格）</h3>
        <p class="page-desc">按保留规则当前已生效版本实时判断；已废止规则仅供查询、不参与清理，例外清单内的记录保留。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="loadCleanupResults">刷新清理结论</button>
      </div>
    </header>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in cleanupColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in cleanupRows" :key="String(row['记录编号'])">
          <td>{{ row['记录编号'] }}</td>
          <td>{{ row['表名称'] }}</td>
          <td>{{ row['来源规则编号'] || '—' }}</td>
          <td><span class="tag" :class="ruleStatusClass(row['规则状态'])">{{ row['规则状态'] }}</span></td>
          <td><span class="tag" :class="eligibilityClass(row['清理资格'])">{{ row['清理资格'] }}</span></td>
          <td>{{ row['判定依据'] }}</td>
        </tr>
        <tr v-if="!cleanupRows.length">
          <td :colspan="cleanupColumns.length" class="empty-state">暂无清理结果数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条表生命周期管理记录 · 清理结果 {{ cleanupRows.length }} 条</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/lifecycle'
const columns = ["策略编号", "表名称", "创建日期", "保留天数", "归档路径", "清理方式", "最后操作", "策略状态"]
const cleanupColumns = ["记录编号", "表名称", "来源规则编号", "规则状态", "清理资格", "判定依据"]
const actions = ["启用策略", "归档表", "删除表"]
const statuses = ["草稿", "生效中", "已到期", "已归档"]
const stats = [{"label": "生命周期策略总数", "value": 0}, {"label": "生效中策略", "value": 0}, {"label": "已到期策略", "value": 0}]

const rows = ref<Row[]>([])
const cleanupRows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function ruleStatusClass(status: unknown): string {
  if (status === '生效中') return 'ok'
  if (status === '已到期') return 'warn'
  if (status === '已废止') return 'bad'
  return 'muted'
}

function eligibilityClass(value: unknown): string {
  if (value === '可清理') return 'warn'
  if (value === '例外保留') return 'ok'
  return 'muted'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '生命周期策略登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('表生命周期管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '表生命周期管理操作失败'
  }
}

async function loadCleanupResults() {
  try {
    const response = await request(`${ENDPOINT}/cleanup-results`)
    if (!response.ok) {
      throw new Error('生命周期清理结果读取失败')
    }
    const payload = await response.json()
    cleanupRows.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '生命周期清理结果读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('生命周期策略列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    // 动作状态变化后同步刷新清理结论，避免页面残留旧口径
    await loadCleanupResults()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '表生命周期管理列表读取失败'
  }
}

onMounted(reload)
</script>
