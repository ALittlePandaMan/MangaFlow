// Canvas marks sit on the same original artwork in both themes. Keep their
// colors stable so changing the app theme never changes the editing preview.
export const canvasPalette = {
  selected: '#3979cf',
  selectedLabel: '#2563b9',
  detected: '#618bb8',
  hidden: '#868b93',
  selectionFill: 'rgba(57,121,207,.08)',
  marqueeFill: 'rgba(57,121,207,.1)',
  handleBorder: '#1e4f93',
  handleFill: '#f5f9ff',
} as const
