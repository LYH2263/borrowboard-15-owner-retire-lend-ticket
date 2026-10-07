<template>
  <div style="padding:16px;max-width:560px">
    <h1>物主一览</h1>
    <div class="muted">共 {{ count }} 户（无主脏数据另列，不计入人数）</div>

    <div v-for="g in owners" :key="g.name || '__blank__'" class="item" :class="{ dirty: g.is_blank }">
      <strong>{{ g.name || '无主（脏数据）' }}</strong>
      <div class="muted">
        {{ g.items }} 件 · 可借 {{ g.available }} · 在借 {{ g.on_loan }}
        <span v-if="g.is_blank">— 无主不得并入任何户</span>
      </div>
    </div>

    <h2>户名合并</h2>
    <p class="muted">把「旧名（并入方）」并进「存续户名」。合并后新上架只认存续户名，旧名物品与在借行统一改写为新户名。</p>
    <label>旧名（被并入，将消失）
      <select v-model="source">
        <option value="" disabled>选择旧名</option>
        <option v-for="g in mergeable" :key="'s'+g.name" :value="g.name">{{ g.name }}（{{ g.items }}件）</option>
      </select>
    </label>
    <label>存续户名（保留）
      <select v-model="target">
        <option value="" disabled>选择存续户名</option>
        <option v-for="g in mergeable.filter(x => x.name !== source)" :key="'t'+g.name" :value="g.name">
          {{ g.name }}（{{ g.items }}件）
        </option>
      </select>
    </label>
    <input v-model="newName" placeholder="或输入新的存续户名，如 李四家（二选一）" />
    <button :disabled="!canMerge" @click="doMerge">合并</button>
    <div v-if="msg" class="msg" :class="okMsg ? 'ok' : 'err'">{{ msg }}</div>
  </div>
</template>
<script setup>
import { computed, inject, ref, onMounted } from 'vue'
import { api } from '../api'
const owners = ref([])
const count = ref(0)
const source = ref('')
const target = ref('')
const newName = ref('')
const msg = ref('')
const okMsg = ref(false)
const reloadBoard = inject('reloadBoard')
const mergeable = computed(() => owners.value.filter(g => !g.is_blank))
const effectiveTarget = computed(() => newName.value.trim() || target.value)
const canMerge = computed(() =>
  source.value && effectiveTarget.value && source.value !== effectiveTarget.value.trim())
async function load() {
  const o = await api('/owners')
  owners.value = o.owners
  count.value = o.count
}
onMounted(load)
async function doMerge() {
  msg.value = ''
  const tgt = effectiveTarget.value.trim()
  try {
    const r = await api('/owners/merge', {
      method: 'POST', body: JSON.stringify({ source: source.value, target: tgt }),
    })
    msg.value = `已把「${r.source}」并入「${r.target}」，改写 ${r.moved_items} 件；旧名再上架将自动记为 ${r.target}。`
    okMsg.value = true
    source.value = target.value = newName.value = ''
    await Promise.all([load(), reloadBoard()])
  } catch (e) {
    msg.value = '合并失败，已回到合并前：' + e.message
    okMsg.value = false
  }
}
</script>
<style scoped>
.dirty { border-style: dashed; opacity: .8; }
label { display: block; font-size: 13px; margin-top: 8px; }
.msg { margin-top: 10px; font-size: 13px; padding: 8px 10px; }
.msg.err { background: #f3d9d0; border: 1px solid #8a3b2a; }
.msg.ok { background: #dfeadd; border: 1px solid #3b6a3b; }
button:disabled { opacity: .5; cursor: not-allowed; }
</style>
