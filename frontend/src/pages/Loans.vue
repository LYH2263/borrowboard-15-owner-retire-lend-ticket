<template>
  <div style="padding:16px">
    <h1>借还记录</h1>
    <h3>逾期</h3>
    <div v-for="l in f(data.overdue)" :key="'o'+l.id" class="item overdue">
      {{ l.title }} · {{ l.borrower }} <span class="muted">（物主 {{ l.owner || '—' }}）</span>
    </div>
    <h3>在借</h3>
    <div v-for="l in f(data.active)" :key="'a'+l.id" class="item">
      {{ l.title }} · {{ l.borrower }} <span class="muted">（物主 {{ l.owner || '—' }}）</span>
    </div>
    <h3>已还</h3>
    <div v-for="l in f(data.returned)" :key="'r'+l.id" class="item">
      {{ l.title }} · {{ l.borrower }} <span class="muted">（物主 {{ l.owner || '—' }}）</span>
    </div>
  </div>
</template>
<script setup>
import { inject, ref, onMounted } from 'vue'
import { api } from '../api'
const matchOwner = inject('matchOwner')
const data = ref({ active: [], overdue: [], returned: [] })
const f = (xs) => xs.filter(l => matchOwner(l.owner))
onMounted(async () => { data.value = await api('/loans') })
</script>
