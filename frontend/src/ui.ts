export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export const buttonClass = 'inline-flex min-h-[38px] cursor-pointer items-center justify-center gap-2 rounded-lg border border-line bg-raised px-3 py-0 text-xs font-semibold leading-none text-secondary transition-colors duration-150 hover:border-line-strong hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-[.38] focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-accent/15'
export const primaryButtonClass = cn(buttonClass, '!border-accent !bg-accent font-bold !text-[#06241c] hover:!border-accent-hover hover:!bg-accent-hover')
export const dangerButtonClass = cn(buttonClass, '!bg-transparent !text-[#d19a95] hover:!border-danger/60 hover:!bg-danger/25 hover:!text-white')
export const iconButtonClass = cn(buttonClass, '!size-8 !min-h-8 !p-0')

export const inputClass = 'h-[38px] w-full rounded-lg border border-line bg-canvas px-3 py-0 text-xs leading-normal text-ink outline-none transition duration-150 placeholder:text-disabled hover:border-line-strong focus:border-accent focus:ring-3 focus:ring-accent/15 read-only:cursor-default read-only:bg-panel read-only:text-muted disabled:cursor-not-allowed disabled:opacity-50'
export const textareaClass = cn(inputClass, 'h-auto min-h-18 resize-none py-3')
export const fieldLabelClass = 'min-w-0 text-[10px] text-secondary'

export const surfaceCardClass = 'rounded-xl border border-line bg-panel'
export const floatingPanelClass = 'rounded-xl border border-line-strong bg-[rgb(24_27_23/.96)] text-secondary shadow-panel backdrop-blur-xl'
export const pageClass = 'h-full overflow-auto px-[max(40px,calc((100vw-1240px)/2))] pb-16 pt-10 max-[1200px]:px-8'
export const eyebrowClass = 'font-mono text-[10px] font-medium tracking-[2px] text-accent'
export const sectionTitleClass = 'mb-3 text-base font-semibold text-ink'

export const toastBaseClass = 'fixed left-1/2 top-3 z-[120] flex max-w-[min(760px,calc(100vw-32px))] -translate-x-1/2 items-center gap-3 rounded-lg px-4 py-3 text-xs shadow-panel'

export const scrollbarClass = '[scrollbar-color:#44443e_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar]:size-2.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:border-[3px] [&::-webkit-scrollbar-thumb]:border-transparent [&::-webkit-scrollbar-thumb]:bg-[#44443e] [&::-webkit-scrollbar-thumb]:bg-clip-padding [&::-webkit-scrollbar-thumb:hover]:bg-[#5d5d54] [&::-webkit-scrollbar-corner]:bg-transparent [&_*]:[scrollbar-color:#44443e_transparent] [&_*]:[scrollbar-width:thin] [&_*::-webkit-scrollbar]:size-2.5 [&_*::-webkit-scrollbar-track]:bg-transparent [&_*::-webkit-scrollbar-thumb]:rounded-full [&_*::-webkit-scrollbar-thumb]:border-[3px] [&_*::-webkit-scrollbar-thumb]:border-transparent [&_*::-webkit-scrollbar-thumb]:bg-[#44443e] [&_*::-webkit-scrollbar-thumb]:bg-clip-padding [&_*::-webkit-scrollbar-thumb:hover]:bg-[#5d5d54] [&_*::-webkit-scrollbar-corner]:bg-transparent'
