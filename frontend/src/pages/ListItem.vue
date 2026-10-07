<template>
  <div style="padding:16px;max-width:420px">
    <h1>上架</h1>
    <input v-model="title" placeholder="物品名" />
    <input v-model="owner" list="owner-names" placeholder="物主" />
    <datalist id="owner-names">
      <option v-for="g in groups" :key="g.name || '__b'" :value="g.name || '无主'"></option>
    </datalist>
    <button @click="go">上架</button>
    <div v-if="msg" class="muted" style="margin-top:8px">{{ msg }}</div>
  </div>
</template>
<script setup>
import { inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
const router = useRouter()
const title = ref('')
const owner = ref('')
const msg = ref('')
const groups = inject('ownerGroups', { value: [] })
async function go() {
  msg.value = ''
  const r = await api('/items', { method: 'POST', body: JSON.stringify({ title: title.value, owner: owner.value }) })
  if (r.rewritten_from) {
    msg.value = `「${r.rewritten_from}」已并入「${r.owner}」，本件按 ${r.owner} 上账（旧名不另立户）。`
  } else {
    router.push('/')
  }
  title.value = ''
}
</script>
