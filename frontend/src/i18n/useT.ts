import { useMemo } from 'react'
import zh from './zh-CN.json'
import en from './en.json'

export type I18nLanguage = 'zh-CN' | 'en'

const dictionaries: Record<I18nLanguage, Record<string, string>> = {
  'zh-CN': zh,
  en,
}

export function useT(language: I18nLanguage) {
  return useMemo(() => {
    const dictionary = dictionaries[language] ?? dictionaries['zh-CN']
    return (key: string, params?: Record<string, string | number | null | undefined>) => {
      let value = dictionary[key] ?? dictionaries['zh-CN'][key] ?? key
      if (params) {
        Object.entries(params).forEach(([name, replacement]) => {
          value = value.split(`{${name}}`).join(replacement === null || replacement === undefined ? '--' : String(replacement))
        })
      }
      return value
    }
  }, [language])
}
