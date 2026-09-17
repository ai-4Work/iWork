/** 全局轻提示：#toast 容器由 AppLayout 渲染，这里只负责填字与显示时长。 */
export function showToast(msg: string): void {
  const el = document.getElementById('toast')
  if (!el) return
  el.textContent = msg
  el.classList.add('show')
  setTimeout(() => el.classList.remove('show'), 2500)
}
