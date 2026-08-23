/** 错误横幅：页内错误提示 + 可选重试（唯一新增展示组件） */
interface Props {
  text: string
  onRetry?: () => void
  retrying?: boolean
}

export default function Notice({ text, onRetry, retrying }: Props) {
  return (
    <div className="notice" role="alert">
      <span className="notice-msg">⚠ {text}</span>
      {onRetry && (
        <button className="btn notice-btn" onClick={onRetry} disabled={retrying}>
          {retrying ? '重试中…' : '重试'}
        </button>
      )}
    </div>
  )
}
