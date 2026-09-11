'use client'

import { Button } from '@langgenius/dify-ui/button'
import { Textarea } from '@langgenius/dify-ui/textarea'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'

type Props = {
  value: string
  label: string
  placeholder?: string
  busy?: boolean
  disabled?: boolean
  onChange: (value: string) => void
  onSend: (value: string) => void
}

export function WorkbenchComposer({
  value,
  label,
  placeholder,
  busy = false,
  disabled = false,
  onChange,
  onSend,
}: Props) {
  const { t } = useTranslation('common')
  const composingRef = useRef(false)
  const canSend = !busy && !disabled && value.trim().length > 0
  const send = () => {
    if (canSend && !composingRef.current) onSend(value)
  }

  return (
    <form
      className="space-y-2 rounded-xl border border-divider-subtle bg-background-default p-3"
      aria-busy={busy}
      onSubmit={(event) => {
        event.preventDefault()
        send()
      }}
    >
      <Textarea
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onValueChange={(nextValue) => onChange(nextValue)}
        disabled={disabled}
        rows={4}
        className="field-sizing-content max-h-60 min-h-28 resize-none"
        onCompositionStart={() => {
          composingRef.current = true
        }}
        onCompositionEnd={() => {
          composingRef.current = false
        }}
        onKeyDown={(event) => {
          if (
            event.key !== 'Enter' ||
            event.shiftKey ||
            event.nativeEvent.isComposing ||
            composingRef.current ||
            event.keyCode === 229
          )
            return
          event.preventDefault()
          send()
        }}
      />
      <div className="flex justify-end">
        <Button type="submit" disabled={!canSend}>
          {t(($) => $['operation.send'])}
        </Button>
      </div>
    </form>
  )
}
