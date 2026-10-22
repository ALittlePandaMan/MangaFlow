import {t} from '../i18n'
export const DEFAULT_FONT_FAMILIES = [
  {name: 'Noto Sans CJK SC', get label(){return t("Noto Sans CJK SC（默认黑体）")}},
  {name: 'Noto Sans CJK Bold', get label(){return t("Noto Sans CJK Bold（粗黑体）")}},
  {name: 'Noto Serif CJK Regular', get label(){return t("Noto Serif CJK Regular（宋体）")}},
  {name: 'Noto Serif CJK Bold', get label(){return t("Noto Serif CJK Bold（粗宋体）")}},
] as const
