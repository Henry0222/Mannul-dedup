import assert from 'node:assert/strict'
import test from 'node:test'
import {clusterCandidates, editorFromGraph, editorSvg, equalSpacingByCluster,
  graphFromEditor, translateNodes} from '../src/editor_model.mjs'

function graph(items, links=[]) {
  return {network:{items:items.map(([id,label,x,y,cluster]) => ({id,label,x,y,cluster,
    weights:{Documents:2}})), links}, config:{parameters:{attraction:2}}}
}

test('manual nodes and their styles survive a VOS graph round trip', () => {
  const original = graph([[1,'A & B',0,0,1],[2,'C',2,1,1]],
    [{source_id:1,target_id:2,strength:3}])
  let editor = editorFromGraph(original)
  editor = translateNodes(editor, [1], 50, -20)
  editor.items[0].color = '#123456'
  editor.items[0].labelDx = 12
  editor.items[0].cluster = 2
  editor.links[0].visible = false
  const saved = graphFromEditor(original, editor)
  const restored = editorFromGraph(saved)
  assert.equal(restored.items[0].x, editor.items[0].x)
  assert.equal(restored.items[0].color, '#123456')
  assert.equal(restored.items[0].labelDx, 12)
  assert.equal(restored.items[0].cluster, 2)
  assert.equal(restored.links[0].visible, false)
  assert.equal(saved.network.items[0].cluster, 2)
  assert.equal(saved.config.parameters.attraction, 2)
})

test('blank overlap selects the larger cluster first', () => {
  const editor = {items:[
    {id:1,cluster:1,x:100,y:100,visible:true}, {id:2,cluster:1,x:300,y:100,visible:true},
    {id:3,cluster:1,x:300,y:300,visible:true}, {id:4,cluster:1,x:100,y:300,visible:true},
    {id:5,cluster:2,x:170,y:170,visible:true}, {id:6,cluster:2,x:230,y:170,visible:true},
    {id:7,cluster:2,x:200,y:230,visible:true}
  ], links:[]}
  assert.deepEqual(clusterCandidates(editor,{x:200,y:195}),[1,2])
  const moved = translateNodes(editor,[1,2,3,4],20,5)
  assert.equal(moved.items[0].x,120)
  assert.equal(moved.items[4].x,170)
})

test('locked nodes stay in place during group dragging and axis layout', () => {
  const editor = editorFromGraph(graph([[1,'A',0,0,1],[2,'B',1,1,1]]))
  editor.items[0].locked = true
  const moved = translateNodes(editor, [1,2], 30, 10)
  assert.equal(moved.items[0].x, editor.items[0].x)
  assert.equal(moved.items[1].x, editor.items[1].x+30)
  assert.equal(equalSpacingByCluster(editor).items[0].x, editor.items[0].x)
  const saved = graphFromEditor(graph([[1,'A',0,0,1],[2,'B',1,1,1]]), moved)
  assert.equal(editorFromGraph(saved).items[0].locked, true)
})

test('node movement carries its label and connected line in the exported drawing', () => {
  const original = editorFromGraph(graph([[1,'A',100,120,1],[2,'B',240,120,1]],
    [{source_id:1,target_id:2,strength:1}]))
  const moved = translateNodes(original, [1], 30, 20)
  const svg = editorSvg(moved)
  const edge = svg.match(/<line id="edge-1:2" x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"/)
  const label = svg.match(/<text id="label-1" x="([\d.]+)" y="([\d.]+)"/)
  assert.ok(edge && label)
  assert.equal(Number(edge[1]), moved.items[0].x)
  assert.equal(Number(edge[2]), moved.items[0].y)
  assert.equal(Number(edge[3]), moved.items[1].x)
  assert.equal(Number(edge[4]), moved.items[1].y)
  assert.equal(Number(label[1]), moved.items[0].x + moved.items[0].labelDx)
  assert.equal(Number(label[2]), moved.items[0].y + moved.items[0].labelDy)
})

test('equal spacing places each cluster on its own evenly spaced horizontal row', () => {
  const editor = editorFromGraph(graph([
    [1,'A',1,1,1],[2,'B',2,2,1],[3,'C',3,3,1],
    [4,'D',4,4,2],[5,'E',5,5,2],[6,'F',6,6,3]
  ]))
  const aligned = equalSpacingByCluster(editor)
  const groups = new Map()
  for (const item of aligned.items) {
    if (!groups.has(item.cluster)) groups.set(item.cluster,[])
    groups.get(item.cluster).push(item)
  }
  const rows = [...groups.values()]
  assert.equal(new Set(rows[0].map(item => item.y)).size,1)
  assert.equal(new Set(rows[1].map(item => item.y)).size,1)
  assert.equal(rows[1][0].y-rows[0][0].y,rows[2][0].y-rows[1][0].y)
  assert.equal(rows[0][1].x-rows[0][0].x,rows[0][2].x-rows[0][1].x)
})

test('SVG keeps editable text and hides excluded graphics', () => {
  const editor = editorFromGraph(graph([[1,'A & B',0,0,1],[2,'C',1,1,1]],
    [{source_id:1,target_id:2,strength:1}]))
  editor.items[1].visible = false
  const svg = editorSvg(editor)
  assert.match(svg,/<g id="nodes">/)
  assert.match(svg,/<text id="label-1"/)
  assert.match(svg,/A &amp; B/)
  assert.doesNotMatch(svg,/node-2/)
  assert.doesNotMatch(svg,/edge-1:2/)
})
