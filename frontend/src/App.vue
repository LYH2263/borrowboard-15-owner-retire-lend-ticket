<template>
  <div>
    <div class="status-bar">
      <span>可借 {{ counts.available || 0 }}</span>
      <span>在借 {{ counts.active || 0 }}</span>
      <span>逾期 {{ counts.overdue || 0 }}</span>
      <span class="filter">
        物主筛选
        <select v-model="ui.ownerFilter" @change="load">
          <option value="">全部</option>
          <option v-for="g in groups" :key="g.name || BLANK" :value="g.is_blank ? BLANK : g.name">
            {{ g.name || '无主（脏数据）' }}{{ g.is_blank ? '' : ` · ${g.items}件` }}
          </option>
        </select>
      </span>
      <span class="spacer"></span>
      <span>物主人数 {{ ownerCount }}</span>
    </div>
    <nav class="topnav">
      <router-link to="/">看板</router-link>
      <router-link to="/list">上架</router-link>
      <router-link to="/loans">借还记录</router-link>
      <router-link to="/owners">物主</router-link>
      <router-link to="/settings">设置</router-link>
    </nav>
    <router-view @refresh="load" />
  </div>
</template>
<script setup>
import { ref, onMounted, provide } from 'vue'
import { api } from './api'
import { ui, BLANK_OWNER as BLANK } from './store'
const counts = ref({})
const board = ref({ available: [], active: [], overdue: [] })
const groups = ref([])
const ownerCount = ref(0)
function matchOwner(owner) {
  if (!ui.ownerFilter) return true
  if (ui.ownerFilter === BLANK) return !owner
  return owner === ui.ownerFilter
}
provide('board', board)
provide('reloadBoard', load)
provide('ownerGroups', groups)
provide('matchOwner', matchOwner)
async function load() {
  const [b, o] = await Promise.all([api('/board'), api('/owners')])
  board.value = b
  counts.value = b.counts || {}
  groups.value = o.owners || []
  ownerCount.value = o.count ?? 0
  // 若筛选里选的户刚被合并走，复位到全部，避免合并后看起来“空了”
  if (ui.ownerFilter && !groups.value.some(
        g => (g.is_blank ? BLANK : g.name) === ui.ownerFilter)) {
    ui.ownerFilter = ''
  }
}
onMounted(load)
</script>
<style scoped>
.filter { display: flex; align-items: center; gap: 6px; }
.filter select { width: auto; margin: 0; padding: 2px 4px; }
.spacer { flex: 1; }
.status-bar span { white-space: nowrap; }
</style>
