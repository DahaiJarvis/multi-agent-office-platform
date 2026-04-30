import { ref, computed } from 'vue'

export function usePagination(options: {
  pageSize?: number
  fetchFn: (offset: number, limit: number) => Promise<void>
}) {
  const pageSize = options.pageSize ?? 20
  const offset = ref(0)
  const loading = ref(false)

  const currentPage = computed(() => Math.floor(offset.value / pageSize) + 1)

  function prevPage() {
    offset.value = Math.max(0, offset.value - pageSize)
    return options.fetchFn(offset.value, pageSize)
  }

  function nextPage() {
    offset.value += pageSize
    return options.fetchFn(offset.value, pageSize)
  }

  function goToPage(page: number) {
    offset.value = Math.max(0, (page - 1) * pageSize)
    return options.fetchFn(offset.value, pageSize)
  }

  function reset() {
    offset.value = 0
    return options.fetchFn(0, pageSize)
  }

  return {
    offset,
    pageSize,
    currentPage,
    loading,
    prevPage,
    nextPage,
    goToPage,
    reset,
  }
}
