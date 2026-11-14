<template>
  <section class="page" data-module="retention">
    <header class="page-head">
      <div>
        <h2>数据保留策略管理</h2>
        <p class="page-desc">维护数据保留的期限设定、过期动作与合规依据；规则按 草稿→生效中→已到期→已废止 严格流转，例外清单与合规依据的每次修改都会冻结旧版本并写入审计。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出数据保留策略清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload(1)">
      <label class="filter-item">
        <span>规则编号</span>
        <input v-model="filters.keyword" placeholder="按规则编号检索" />
      </label>
      <label class="filter-item">
        <span>规则状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>例外清单</span>
        <input v-model="filters.exception" placeholder="按例外清单筛选并定位受影响记录" />
      </label>
      <label class="filter-item">
        <span>合规依据</span>
        <input v-model="filters.basis" placeholder="按合规依据筛选" />
      </label>
      <label class="filter-item">
        <span>合规依据排序</span>
        <select v-model="filters.sort">
          <option value="">不排序</option>
          <option value="合规依据">升序</option>
          <option value="-合规依据">降序</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>受影响记录</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['规则编号'] ?? '—' }}</td>
          <td>{{ row['数据集范围'] ?? '—' }}</td>
          <td>{{ row['保留期限'] ?? '—' }}</td>
          <td>{{ row['过期动作'] ?? '—' }}</td>
          <td>
            <ul v-if="toList(row['例外清单']).length" class="exception-list">
              <li v-for="item in toList(row['例外清单'])" :key="item">{{ item }}</li>
            </ul>
            <span v-else class="muted-text">无例外</span>
          </td>
          <td>{{ row['合规依据'] ?? '—' }}</td>
          <td>{{ row['执行周期'] ?? '—' }}</td>
          <td><span class="tag" :class="statusClass(row.status)">{{ row.status }}</span></td>
          <td>
            <button class="link" type="button" @click="openDetail(row)">
              {{ row.affected_records?.length ?? 0 }} 条
            </button>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action.name"
              class="link"
              type="button"
              :disabled="!action.enabled(row.status)"
              :title="action.enabled(row.status) ? action.name : action.reason"
              @click="runAction(action.name, row)"
            >
              {{ action.name }}
            </button>
            <button class="link" type="button" @click="openEdit(row)">修改依据</button>
            <button class="link" type="button" @click="openDetail(row)">版本/详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的保留规则</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <div class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="reload(page - 1)">上一页</button>
        <span>第 {{ page }} 页 / 共 {{ totalPages }} 页（共 {{ total }} 条，每页 {{ size }} 条）</span>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="reload(page + 1)">下一页</button>
        <select v-model.number="size" @change="reload(1)">
          <option :value="5">5 条/页</option>
          <option :value="10">10 条/页</option>
          <option :value="20">20 条/页</option>
        </select>
      </div>
      <span v-if="message" :class="messageKind === 'error' ? 'error-text' : 'success-text'">{{ message }}</span>
    </footer>

    <!-- 修改例外清单 / 合规依据：后端冻结旧版本并写审计事件 -->
    <div v-if="editing" class="modal-mask" @click.self="editing = null">
      <div class="modal">
        <div class="modal-head">
          <h3>修改规则 {{ editing['规则编号'] }}（当前 v{{ editing.current_version?.version }}）</h3>
          <button class="btn ghost" type="button" @click="editing = null">关闭</button>
        </div>
        <p class="muted-text">已废止规则只读；其余规则提交后会冻结当前版本、生成新版本，并为每个变更字段生成审计事件。</p>
        <div class="form-grid">
          <label class="full">
            <span>数据集范围</span>
            <input v-model="editForm['数据集范围']" />
          </label>
          <label>
            <span>保留期限</span>
            <input v-model="editForm['保留期限']" />
          </label>
          <label>
            <span>过期动作</span>
            <input v-model="editForm['过期动作']" />
          </label>
          <label class="full">
            <span>例外清单（多个用逗号或顿号分隔）</span>
            <textarea v-model="editForm['例外清单']" rows="2"></textarea>
          </label>
          <label class="full">
            <span>合规依据</span>
            <input v-model="editForm['合规依据']" />
          </label>
          <label>
            <span>执行周期</span>
            <input v-model="editForm['执行周期']" />
          </label>
          <label>
            <span>操作人</span>
            <input v-model="editForm.operator" placeholder="默认系统" />
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="editing = null">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitEdit">{{ saving ? '提交中…' : '冻结旧版并提交' }}</button>
        </div>
      </div>
    </div>

    <!-- 详情：版本链（旧版清单/旧期限）+ 受影响回收记录 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ detail['规则编号'] }} · 版本与受影响记录</h3>
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
        </div>
        <h4>版本链（历史版本已冻结）</h4>
        <div
          v-for="version in detail.versions ?? []"
          :key="version.version"
          class="version-card"
          :class="{ current: version['生效状态'] === '当前版本' }"
        >
          <strong>v{{ version.version }}（{{ version['生效状态'] }}<template v-if="version['状态']"> · {{ version['状态'] }}</template>）</strong>
          <dl>
            <dt>保留期限</dt><dd>{{ version['保留期限'] }}</dd>
            <dt>合规依据</dt><dd>{{ version['合规依据'] }}</dd>
            <dt>例外清单</dt>
            <dd>
              <ul v-if="toList(version['例外清单']).length" class="exception-list">
                <li v-for="item in toList(version['例外清单'])" :key="item">{{ item }}</li>
              </ul>
              <span v-else>无例外</span>
            </dd>
            <dt>过期动作</dt><dd>{{ version['过期动作'] }}</dd>
            <dt>执行周期</dt><dd>{{ version['执行周期'] }}</dd>
          </dl>
        </div>
        <h4>受影响回收记录（{{ detail.affected_records?.length ?? 0 }} 条）</h4>
        <table class="data-table">
          <thead>
            <tr><th>记录编号</th><th>表名称</th><th>恢复状态</th><th>清理资格</th></tr>
          </thead>
          <tbody>
            <tr v-for="record in detail.affected_records ?? []" :key="record['记录编号']">
              <td>{{ record['记录编号'] }}</td>
              <td>{{ record['表名称'] }}</td>
              <td>{{ record['恢复状态'] }}</td>
              <td><span class="tag" :class="eligibilityClass(record['清理资格'])">{{ record['清理资格'] || '待判定' }}</span></td>
            </tr>
            <tr v-if="!(detail.affected_records?.length)">
              <td colspan="4" class="empty-state">暂无挂接到本规则的回收记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

interface ActionMeta {
  name: string
  enabled: (status: string) => boolean
  reason: string
}

const ENDPOINT = '/api/retention'
const columns = ['规则编号', '数据集范围', '保留期限', '过期动作', '例外清单', '合规依据', '执行周期', '规则状态']
const statuses = ['草稿', '生效中', '已到期', '已废止']
// 动作与后端状态机保持一致：只能推进到下一个状态，废止为终态
const actions: ActionMeta[] = [
  { name: '启用规则', enabled: (status) => status === '草稿', reason: '仅草稿规则可启用' },
  { name: '归档规则', enabled: (status) => status === '生效中', reason: '仅生效中规则可归档' },
  { name: '废止规则', enabled: (status) => status === '已到期', reason: '仅已到期规则可废止，废止为终态' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(10)
const totalPages = ref(1)
const message = ref('')
const messageKind = ref<'info' | 'error'>('info')
const filters = ref<Record<string, string>>({ keyword: '', status: '', exception: '', basis: '', sort: '' })

const editing = ref<Row | null>(null)
const editForm = ref<Record<string, string>>({})
const saving = ref(false)
const detail = ref<Row | null>(null)

const stats = ref([
  { label: '保留规则总数', value: 0 },
  { label: '生效中规则', value: 0 },
  { label: '已废止（只读）', value: 0 },
])

function toList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String)
  if (typeof value === 'string' && value.trim()) {
    return value.split(/[,，、;；\n]/).map((item) => item.trim()).filter(Boolean)
  }
  return []
}

function statusClass(status: string): string {
  if (status === '生效中') return 'ok'
  if (status === '已到期') return 'warn'
  if (status === '已废止') return 'bad'
  return 'muted'
}

function eligibilityClass(value?: string): string {
  if (value === '可清理') return 'warn'
  if (value === '例外保留') return 'ok'
  return 'muted'
}

function resetFilters() {
  filters.value = { keyword: '', status: '', exception: '', basis: '', sort: '' }
  void reload(1)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function resolveError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    if (payload?.detail) {
      return typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
    }
    if (payload?.message) return payload.message
  } catch {
    // 响应体不是 JSON 时退回通用提示
  }
  return fallback
}

async function reload(targetPage?: number) {
  if (targetPage) page.value = targetPage
  message.value = ''
  const params = new URLSearchParams()
  Object.entries(filters.value).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  params.set('page', String(page.value))
  params.set('size', String(size.value))
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error(await resolveError(response, '保留规则列表读取失败'))
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? 0
    totalPages.value = Math.max(1, Math.ceil(total.value / size.value))
    stats.value = [
      { label: '保留规则总数', value: total.value },
      { label: '生效中规则', value: rows.value.filter((row) => row.status === '生效中').length },
      { label: '已废止（只读）', value: rows.value.filter((row) => row.status === '已废止').length },
    ]
  } catch (error) {
    messageKind.value = 'error'
    message.value = error instanceof Error ? error.message : '数据保留策略列表读取失败'
  }
}

async function runAction(actionName: string, row: Row) {
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: actionName } }),
    })
    const payload = await response.json()
    if (!response.ok) {
      // 409 冲突：重复执行/并发争抢，后端会说明冲突原因
      messageKind.value = 'error'
      message.value = payload?.detail ? String(payload.detail) : '动作未生效，请稍后重试'
      return
    }
    if (!payload.ok) {
      messageKind.value = 'error'
      message.value = payload.message
      return
    }
    messageKind.value = 'info'
    message.value = payload.message
    detail.value = null
    await reload(page.value)
  } catch (error) {
    messageKind.value = 'error'
    message.value = error instanceof Error ? error.message : '数据保留策略操作失败'
  }
}

function openEdit(row: Row) {
  if (row.status === '已废止') {
    messageKind.value = 'error'
    message.value = `规则 ${row['规则编号']} 已废止，仅供查询，不能修改`
    return
  }
  editing.value = row
  editForm.value = {
    '数据集范围': row['数据集范围'] ?? '',
    '保留期限': row['保留期限'] ?? '',
    '过期动作': row['过期动作'] ?? '',
    '例外清单': toList(row['例外清单']).join('，'),
    '合规依据': row['合规依据'] ?? '',
    '执行周期': row['执行周期'] ?? '',
    operator: '',
  }
}

async function submitEdit() {
  if (!editing.value) return
  saving.value = true
  try {
    const response = await request(`${ENDPOINT}/${editing.value.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ values: { ...editForm.value } }),
    })
    const payload = await response.json()
    if (!response.ok) {
      messageKind.value = 'error'
      message.value = payload?.detail ? String(payload.detail) : '修改未生效'
      return
    }
    messageKind.value = 'info'
    message.value = payload.message
    editing.value = null
    await reload(page.value)
  } catch (error) {
    messageKind.value = 'error'
    message.value = error instanceof Error ? error.message : '修改请求失败'
  } finally {
    saving.value = false
  }
}

async function openDetail(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error(await resolveError(response, '规则详情读取失败'))
    detail.value = await response.json()
  } catch (error) {
    messageKind.value = 'error'
    message.value = error instanceof Error ? error.message : '规则详情读取失败'
  }
}

onMounted(() => void reload(1))
</script>
