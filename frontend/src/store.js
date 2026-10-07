import { reactive } from 'vue'

// 顶细条物主筛选的全局状态；'' = 全部，BLANK = 无主脏数据
export const BLANK_OWNER = '__blank__'
export const ui = reactive({ ownerFilter: '' })
