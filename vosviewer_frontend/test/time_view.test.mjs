import assert from 'node:assert/strict'
import test from 'node:test'
import { TIME_COLORS, savedViewMode, withTimePalette, yearExtent, yearPalette } from '../src/time_view.mjs'

test('time coloring keeps the same nodes, links and positions', () => {
  const graph = { network: { items: [
    { id: 1, x: 1.2, y: 3, scores: { 'Avg. pub. year': 2001 } },
    { id: 2, x: 4, y: 5, scores: { 'Avg. pub. year': 2024 } }
  ], links: [{ source_id: 1, target_id: 2, strength: 3 }] },
  config: { parameters: { item_color: 1, attraction: 4 } } }
  const time = withTimePalette(graph)
  assert.equal(time.network, graph.network)
  assert.deepEqual(yearExtent(time), [2001, 2024])
  assert.equal(time.config.parameters.attraction, 4)
  assert.equal(time.config.parameters.min_score, 2001)
  assert.equal(time.config.parameters.max_score, 2024)
  assert.equal(time.config.color_schemes.score_colors[0].color, TIME_COLORS[0].color)
  assert.equal(time.config.color_schemes.score_colors.at(-1).color, TIME_COLORS.at(-1).color)
  assert.equal(TIME_COLORS[0].color, '#0A5264')
  assert.equal(TIME_COLORS.at(-1).color, '#F7DC4B')
  assert.equal(savedViewMode(time), 'network')
  assert.equal(savedViewMode({ ...time, config: { parameters: { item_color: 2 } } }), 'time')
  assert.equal(graph.config.color_schemes, undefined)
})

test('closely spaced years receive visibly different palette stops', () => {
  const graph = {network: {items: [2019, 2020, 2020.2, 2020.3, 2024]
    .map(year => ({scores: {'Avg. pub. year': year}}))}}
  const colors = yearPalette(graph)
  assert.equal(colors.length, 5)
  assert.equal(colors[1].rescaled_score, 0.2)
  assert.ok(Math.abs(colors[2].rescaled_score - 0.24) < 1e-9)
  assert.ok(Math.abs(colors[3].rescaled_score - 0.26) < 1e-9)
  assert.equal(colors[1].color, '#3563B3')
  assert.equal(colors[2].color, '#0AA9B3')
  assert.equal(colors[3].color, '#68CA69')
})

test('graph without publication years leaves native palette untouched', () => {
  const graph = { network: { items: [{ id: 1 }, { id: 2 }], links: [] } }
  assert.equal(yearExtent(graph), null)
  assert.equal(withTimePalette(graph), graph)
})
