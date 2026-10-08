export const TIME_SCORE = 'Avg. pub. year'

export const TIME_COLORS = [
  { rescaled_score: 0, color: '#0A5264' },
  { rescaled_score: 0.25, color: '#3563B3' },
  { rescaled_score: 0.5, color: '#0AA9B3' },
  { rescaled_score: 0.75, color: '#68CA69' },
  { rescaled_score: 1, color: '#F7DC4B' }
]

export function yearPalette(data) {
  const years = [...new Set((data?.network?.items || [])
    .map(item => item.scores?.[TIME_SCORE])
    .filter(value => typeof value === 'number' && Number.isFinite(value)))].sort((a, b) => a - b)
  if (years.length < 2) return TIME_COLORS
  const min = years[0]; const max = years.at(-1)
  if (years.length <= TIME_COLORS.length) {
    return years.map((year, index) => ({
      rescaled_score: (year - min) / (max - min),
      color: TIME_COLORS[Math.round(index * (TIME_COLORS.length - 1) / (years.length - 1))].color
    }))
  }
  return TIME_COLORS.map((stop, index) => {
    const position = index * (years.length - 1) / (TIME_COLORS.length - 1)
    const low = Math.floor(position); const high = Math.ceil(position)
    const year = years[low] + (years[high] - years[low]) * (position - low)
    return { ...stop, rescaled_score: (year - min) / (max - min) }
  })
}

export function yearExtent(data) {
  const years = (data?.network?.items || [])
    .map(item => item.scores?.[TIME_SCORE])
    .filter(value => typeof value === 'number' && Number.isFinite(value))
  return years.length ? [Math.min(...years), Math.max(...years)] : null
}

export function withTimePalette(data) {
  const extent = yearExtent(data)
  if (!extent) return data
  const config = data.config || {}
  const parameters = { ...config.parameters, score_colors: 'Custom' }
  if (extent[0] < extent[1]) {
    parameters.min_score = extent[0]
    parameters.max_score = extent[1]
  }
  return {
    ...data,
    config: {
      ...config,
      parameters,
      color_schemes: { ...config.color_schemes, score_colors: yearPalette(data) }
    }
  }
}

export function savedViewMode(data) {
  return data?.config?.parameters?.item_color === 2 && yearExtent(data) ? 'time' : 'network'
}
