interface Props {
  options: string[]
  value: string
  onChange: (v: string) => void
}

/** 分段按钮组（原型 .seg） */
export default function Seg({ options, value, onChange }: Props) {
  return (
    <span className="seg">
      {options.map(o => (
        <button key={o} className={o === value ? 'on' : ''} onClick={() => onChange(o)}>
          {o}
        </button>
      ))}
    </span>
  )
}
