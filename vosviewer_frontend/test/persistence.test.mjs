import assert from 'node:assert/strict'
import test from 'node:test'
import { mergeSavedParameters, parameterLabels } from '../src/persistence.mjs'

test('layout and clustering edits survive VOS JSON export without changing the network', () => {
  const network = {items: [{id: 1, label: 'China', x: 1.2}], links: []}
  const saved = mergeSavedParameters(JSON.stringify({config: {parameters: {simple_ui: false}}, network}), {
    attraction: 4, repulsion: -2, resolution: 1.7,
    min_cluster_size: 3, merge_small_clusters: true
  })
  assert.deepEqual(saved.network, network)
  assert.equal(saved.config.parameters.simple_ui, false)
  assert.equal(saved.config.parameters.repulsion, -2)
  assert.equal(saved.config.parameters.resolution, 1.7)
  assert.equal(parameterLabels.聚类分辨率, 'resolution')
})
