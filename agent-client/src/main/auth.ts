import { app, ipcMain, safeStorage } from 'electron'
import Store from 'electron-store'

export interface StoredTokens {
  accessToken: string
  refreshToken: string
  /** access token 过期时刻（epoch ms）：渲染进程启动时据此判断要不要先静默刷新 */
  expiresAt: number
}

/** 独立 electron-store 文件，与 settings（config）和业务数据（iwork-data）分开 */
interface WrappedTokens {
  /** encrypted 为 true 时是 encryptString 的 base64，否则是明文 JSON */
  blob: string
  encrypted: boolean
}

/**
 * 登录凭证的安全存储（doc 18-10.3）。
 *
 * 只落主进程：渲染进程拿到的永远是解密后的明文，且不写 localStorage/IndexedDB。
 * 加密交给系统密钥链（macOS Keychain / Windows DPAPI / Linux libsecret）。
 */
export function registerAuth(): { get: () => StoredTokens | null } {
  const store = new Store<{ tokens?: WrappedTokens }>({ name: 'iwork-auth' })

  const canEncrypt = safeStorage.isEncryptionAvailable()
  if (!canEncrypt) {
    // Linux 无 keyring 时恒为 false：dev 允许降级明文，生产拒绝启动
    if (app.isPackaged) {
      throw new Error(
        '系统密钥链不可用，无法安全保存登录凭证。请先安装并启用 libsecret（gnome-keyring / kwallet）。'
      )
    }
    console.warn('[auth] 系统密钥链不可用，开发模式降级为明文存储凭证')
  }

  const read = (): StoredTokens | null => {
    const wrapped = store.get('tokens')
    if (!wrapped) return null
    try {
      const json = wrapped.encrypted
        ? safeStorage.decryptString(Buffer.from(wrapped.blob, 'base64'))
        : wrapped.blob
      return JSON.parse(json) as StoredTokens
    } catch (err) {
      // 换过系统用户/重装系统时密钥链对不上，解不开就按未登录处理，顺手清掉坏数据
      console.error('[auth] 本地凭证解密失败，已清除:', err)
      store.delete('tokens')
      return null
    }
  }

  ipcMain.handle('auth:save', async (_event, tokens: StoredTokens) => {
    const json = JSON.stringify(tokens)
    store.set(
      'tokens',
      canEncrypt
        ? { blob: safeStorage.encryptString(json).toString('base64'), encrypted: true }
        : { blob: json, encrypted: false }
    )
  })

  ipcMain.handle('auth:load', async () => read())

  ipcMain.handle('auth:clear', async () => {
    store.delete('tokens')
  })

  return { get: read }
}
