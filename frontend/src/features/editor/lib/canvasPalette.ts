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

// Shared by original-image and mask polygon previews; keep both tools identical.
export const polygonDraftStyle = {
  stroke: '#ffcb45',
  pointFill: '#fff7d6',
  pointBorder: '#d8a91d',
  selected: canvasPalette.selected,
  firstPointBorder: canvasPalette.handleBorder,
  lineWidth: 2,
  previewWidth: 1.5,
  pointBorderWidth: 1.5,
  firstPointRadius: 5,
  pointRadius: 3.5,
  dash: [6, 4],
  closeDistance: 12,
} as const
