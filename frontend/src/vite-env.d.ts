/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API 基础路径，默认 /api/v1（开发走 Vite proxy，生产走 nginx） */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
