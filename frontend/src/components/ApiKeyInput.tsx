import {useState, type InputHTMLAttributes} from 'react'
import {cn, inputClass} from '../ui'

type ApiKeyInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'autoComplete'> & {
  hasSavedKey: boolean
}

export function ApiKeyInput({hasSavedKey, className, placeholder = '输入 API 密钥', onFocus, onBlur, ...props}: ApiKeyInputProps) {
  const [focused, setFocused] = useState(false)
  // The mask is a placeholder, never a value: FormData must not submit it as a key.
  return <input {...props} className={cn(inputClass,className)} type="password" autoComplete="new-password"
    placeholder={hasSavedKey ? focused ? '' : '••••••••' : placeholder}
    onFocus={event => {setFocused(true); onFocus?.(event)}}
    onBlur={event => {setFocused(false); onBlur?.(event)}}/>
}
