export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export const buttonClass = 'inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-[10px] border border-line bg-raised px-3.5 py-0 text-[13px] font-semibold leading-none text-secondary transition-[background-color,border-color,color,box-shadow] duration-150 hover:border-line-strong hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-[.38] focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-accent/20'
export const primaryButtonClass = cn(buttonClass, '!border-accent !bg-accent font-bold !text-accent-ink hover:!border-accent-hover hover:!bg-accent-hover')
export const dangerButtonClass = cn(buttonClass, '!bg-transparent !text-danger-soft-ink hover:!border-danger/60 hover:!bg-danger/15 hover:!text-danger-soft-ink')
export const iconButtonClass = cn(buttonClass, '!size-9 !min-h-9 !p-0')

export const inputClass = 'h-10 w-full rounded-[10px] border border-line bg-canvas px-3.5 py-0 text-[13px] leading-normal text-ink outline-none transition-[background-color,border-color,box-shadow] duration-150 placeholder:text-disabled hover:border-line-strong focus:border-accent focus:ring-3 focus:ring-accent/20 read-only:cursor-default read-only:bg-panel read-only:text-muted disabled:cursor-not-allowed disabled:opacity-50'
export const textareaClass = cn(inputClass, 'h-auto min-h-20 resize-none py-3')
export const fieldLabelClass = 'min-w-0 text-xs font-medium text-secondary'

export const surfaceCardClass = 'rounded-xl border border-line bg-panel shadow-soft'
export const floatingPanelClass = 'rounded-xl border border-line-strong bg-popover text-secondary shadow-panel backdrop-blur-xl'
export const pageClass = 'h-full overflow-auto px-[max(36px,calc((100vw-1280px)/2))] pb-20 pt-11 max-[1200px]:px-7'
export const eyebrowClass = 'font-mono text-[11px] font-semibold tracking-[1.8px] text-accent'
export const sectionTitleClass = 'mb-4 text-lg font-semibold text-ink'

export const toastBaseClass = 'fixed left-1/2 top-3 z-[120] flex max-w-[min(760px,calc(100vw-32px))] -translate-x-1/2 items-center gap-3 rounded-[10px] px-4 py-3 text-[13px] shadow-panel'

export const scrollbarClass = 'app-scrollbars'
