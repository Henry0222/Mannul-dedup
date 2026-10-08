import React, { useEffect, useRef, useState } from 'react'
import { clusterCandidates, clusterColor, convexHull, editorFromGraph, editorSvg,
  equalSpacingByCluster, graphFromEditor, linkKey, translateNodes } from './editor_model.mjs'

function svgPoint(svg, event) {
  const point = svg.createSVGPoint()
  point.x = event.clientX; point.y = event.clientY
  return point.matrixTransform(svg.getScreenCTM().inverse())
}

function clusterRegions(items) {
  const groups = new Map()
  for (const item of items.filter(item => item.visible)) {
    if (!groups.has(item.cluster)) groups.set(item.cluster, [])
    groups.get(item.cluster).push(item)
  }
  return [...groups.entries()].map(([cluster, members]) => ({cluster, members, hull:convexHull(members)}))
}

export default function NetworkEditor({graph, onClose, onStatus}) {
  const [editor, setEditor] = useState(() => editorFromGraph(graph))
  const editorRef = useRef(editor)
  const [selected, setSelected] = useState([])
  const [historyTick, setHistoryTick] = useState(0)
  const [marquee, setMarquee] = useState(null)
  const past = useRef([]); const future = useRef([])
  const drag = useRef(null); const svgRef = useRef(null)
  const repeatCluster = useRef({point:null, signature:'', index:0})
  const saveTimer = useRef(null); const saveChain = useRef(Promise.resolve())
  const [localStatus, setLocalStatus] = useState('点击节点拖动；Ctrl 点击可多选。拖动聚类内部空白处可移动整组。')

  function update(next) { editorRef.current = next; setEditor(next) }
  function commit(next) {
    if (next === editorRef.current) return
    past.current = [...past.current.slice(-39), editorRef.current]
    future.current = []
    update(next); setHistoryTick(value => value + 1)
  }
  function undo() {
    if (!past.current.length) return
    future.current.push(editorRef.current)
    update(past.current.pop()); setHistoryTick(value => value + 1)
  }
  function redo() {
    if (!future.current.length) return
    past.current.push(editorRef.current)
    update(future.current.pop()); setHistoryTick(value => value + 1)
  }
  function saveNow(current=editorRef.current) {
    const payload = graphFromEditor(graph, current)
    saveChain.current = saveChain.current.catch(() => {}).then(async () => {
      await window.pywebview.api.save_state(JSON.stringify(payload))
      return payload
    })
    return saveChain.current
  }
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      saveNow().catch(error => setLocalStatus(`自动保存失败：${error}`))
    }, 750)
    return () => clearTimeout(saveTimer.current)
  }, [editor])

  const byId = new Map(editor.items.map(item => [item.id, item]))
  const activeItems = editor.items.filter(item => selected.includes(item.id))
  const focus = activeItems[0]
  const clusters = [...new Set(editor.items.map(item => item.cluster))].sort((a, b) => Number(a)-Number(b))
  const hiddenNodes = editor.items.filter(item => !item.visible)
  const hiddenLinks = editor.links.filter(link => !link.visible)

  function editNodes(changes, ids=selected) {
    const picked = new Set(ids)
    commit({...editorRef.current, items:editorRef.current.items.map(item => picked.has(item.id)
      ? {...item, ...changes} : item)})
  }
  function pointerDown(event) {
    if (event.button !== 0 || !svgRef.current) return
    const point = svgPoint(svgRef.current, event)
    const id = event.target.closest?.('[data-node]')?.dataset.node
    if (id) {
      const nodeId = Number(id)
      const ids = (event.ctrlKey || event.metaKey || event.shiftKey)
        ? (selected.includes(nodeId) ? selected.filter(value => value !== nodeId) : [...selected, nodeId])
        : (selected.includes(nodeId) ? selected : [nodeId])
      setSelected(ids)
      if (!ids.includes(nodeId)) return
      if (ids.every(value => editorRef.current.items.find(item => item.id === value)?.locked)) {
        setLocalStatus('所选节点已锁定位置；先取消锁定才能拖动。')
        return
      }
      drag.current = {kind:'nodes', base:editorRef.current, ids, point, moved:false}
      svgRef.current.setPointerCapture(event.pointerId)
      event.preventDefault(); return
    }
    if (event.shiftKey) {
      drag.current = {kind:'marquee', point, moved:false}
      setMarquee({x:point.x, y:point.y, width:0, height:0})
    } else {
      const candidates = clusterCandidates(editorRef.current, point)
      if (!candidates.length) { setSelected([]); setLocalStatus('这里不属于任何聚类；在聚类区域的空白处拖动可移动整组。'); return }
      const signature = candidates.join(',')
      const previous = repeatCluster.current
      const samePlace = previous.point && Math.hypot(previous.point.x-point.x, previous.point.y-point.y) < 24
        && previous.signature === signature
      const index = samePlace ? (previous.index + 1) % candidates.length : 0
      repeatCluster.current = {point, signature, index}
      const cluster = candidates[index]
      const ids = editorRef.current.items.filter(item => item.cluster === cluster).map(item => item.id)
      setSelected(ids)
      setLocalStatus(candidates.length > 1
        ? `重叠区域：正在移动聚类 ${cluster}（${index+1}/${candidates.length}，再次从此处拖动可切换下一层）`
        : `正在移动聚类 ${cluster}，共 ${ids.length} 个节点`)
      drag.current = {kind:'nodes', base:editorRef.current, ids, point, moved:false}
    }
    svgRef.current.setPointerCapture(event.pointerId)
    event.preventDefault()
  }
  function pointerMove(event) {
    const action = drag.current
    if (!action || !svgRef.current) return
    const point = svgPoint(svgRef.current, event)
    const dx = point.x - action.point.x; const dy = point.y - action.point.y
    if (Math.hypot(dx, dy) > 1.5) action.moved = true
    if (action.kind === 'nodes') update(translateNodes(action.base, action.ids, dx, dy))
    if (action.kind === 'marquee') setMarquee({x:Math.min(action.point.x,point.x), y:Math.min(action.point.y,point.y),
      width:Math.abs(dx), height:Math.abs(dy)})
  }
  function pointerUp(event) {
    const action = drag.current
    if (!action) return
    if (action.kind === 'marquee' && marquee) {
      setSelected(editorRef.current.items.filter(item => item.visible && item.x >= marquee.x &&
        item.x <= marquee.x + marquee.width && item.y >= marquee.y && item.y <= marquee.y + marquee.height)
        .map(item => item.id))
      setMarquee(null)
    } else if (action.moved) {
      past.current = [...past.current.slice(-39), action.base]
      future.current = []
      setHistoryTick(value => value + 1)
    }
    drag.current = null
    if (svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId)
  }
  async function closeEditor() {
    try {
      if (saveTimer.current) clearTimeout(saveTimer.current)
      const saved = await saveNow()
      onClose(saved)
    } catch (error) { setLocalStatus(`保存失败：${error}`) }
  }
  async function exportSvg() {
    try {
      await saveNow()
      const result = await window.pywebview.api.save_svg(editorSvg(editorRef.current))
      setLocalStatus(`可编辑 SVG 已保存：${result.path}`); onStatus?.(`可编辑 SVG 已保存：${result.path}`)
    } catch (error) { setLocalStatus(`SVG 导出失败：${error}`) }
  }
  async function exportPng() {
    try {
      await saveNow()
      const blob = new Blob([editorSvg(editorRef.current)], {type:'image/svg+xml;charset=utf-8'})
      const url = URL.createObjectURL(blob)
      const image = new Image()
      try {
        await new Promise((resolve, reject) => { image.onload=resolve; image.onerror=reject; image.src=url })
        const canvas = document.createElement('canvas')
        canvas.width=2400; canvas.height=1680
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height)
        const result = await window.pywebview.api.save_png(canvas.toDataURL('image/png'))
        setLocalStatus(`PNG 已保存：${result.path}`)
      } finally { URL.revokeObjectURL(url) }
    } catch (error) { setLocalStatus(`PNG 导出失败：${error}`) }
  }

  return <div className="network-editor">
    <div className="editor-toolbar">
      <strong>网络图编辑</strong><span>节点拖动 · 空白区域拖动聚类 · Shift 框选 · Ctrl 多选</span>
      <button onClick={undo} disabled={!past.current.length}>撤销</button>
      <button onClick={redo} disabled={!future.current.length}>重做</button>
      <button onClick={() => commit(equalSpacingByCluster(editorRef.current))}>聚类等距横排</button>
      <button onClick={exportSvg}>导出 SVG</button><button onClick={exportPng}>导出 PNG</button>
      <button className="primary" onClick={closeEditor}>完成编辑</button>
    </div>
    <div className="editor-body">
      <aside className="editor-sidebar">
        <h3>选择与样式</h3>
        {focus ? <>
          <p>{activeItems.length === 1 ? focus.label : `已选 ${activeItems.length} 个节点`}</p>
          <label>节点颜色 <input type="color" value={focus.color} onChange={event => editNodes({color:event.target.value})}/></label>
          <label>节点大小 <input type="range" min="4" max="42" value={focus.size} onChange={event => editNodes({size:Number(event.target.value)})}/><output>{Math.round(focus.size)}</output></label>
          <label>所属聚类 <select value={focus.cluster} onChange={event => editNodes({cluster:Number(event.target.value), color:clusterColor(event.target.value)})}>
            {clusters.map(cluster => <option key={cluster} value={cluster}>{cluster}</option>)}
          </select></label>
          <label className="check"><input type="checkbox" checked={focus.locked} onChange={event => editNodes({locked:event.target.checked})}/> 锁定节点位置</label>
          <label>标签水平位置 <input type="number" value={focus.labelDx} onChange={event => editNodes({labelDx:Number(event.target.value)})}/></label>
          <label>标签垂直位置 <input type="number" value={focus.labelDy} onChange={event => editNodes({labelDy:Number(event.target.value)})}/></label>
          <label className="check"><input type="checkbox" checked={focus.labelVisible} onChange={event => editNodes({labelVisible:event.target.checked})}/> 显示标签</label>
          <button onClick={() => {editNodes({visible:false}); setSelected([])}}>隐藏所选节点</button>
        </> : <p>点击节点调整样式；拖动节点或聚类时，标签和连线自动跟随。</p>}
        <h3>恢复隐藏项</h3>
        <p>节点 {hiddenNodes.length} · 连线 {hiddenLinks.length}</p>
        {hiddenNodes.length > 0 && <button onClick={() => commit({...editorRef.current,
          items:editorRef.current.items.map(item => ({...item, visible:true}))})}>恢复全部节点</button>}
        {hiddenNodes.length > 0 && <details><summary>逐项恢复节点</summary>{hiddenNodes.map(item =>
          <button key={item.id} onClick={() => editNodes({visible:true}, [item.id])}>{item.label}</button>)}</details>}
        {hiddenLinks.length > 0 && <button onClick={() => commit({...editorRef.current,
          links:editorRef.current.links.map(item => ({...item, visible:true}))})}>恢复全部连线</button>}
        {hiddenLinks.length > 0 && <details><summary>逐项恢复连线</summary>{hiddenLinks.map(item =>
          <button key={linkKey(item)} onClick={() => commit({...editorRef.current,
            links:editorRef.current.links.map(link => linkKey(link) === linkKey(item)
              ? {...link, visible:true} : link)})}>
            {byId.get(item.source_id)?.label} — {byId.get(item.target_id)?.label}</button>)}</details>}
        <h3>聚类</h3>
        <p>在着色区域的空白处拖动整组；区域重叠时，同一位置重复拖动按节点数从多到少切换。</p>
        <p>“聚类等距横排”让每个聚类各占一条水平线，节点及聚类行都等间距。</p>
      </aside>
      <div className="editor-canvas-wrap">
        <svg ref={svgRef} className="editor-canvas" viewBox="0 0 1000 700"
          onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
          onPointerCancel={pointerUp} role="img" aria-label="可编辑网络图">
          <rect width="1000" height="700" fill="#fff"/>
          <g className="cluster-regions">{clusterRegions(editor.items).map(({cluster, members, hull}) =>
            hull.length >= 3 ? <polygon key={cluster} points={hull.map(point => `${point.x},${point.y}`).join(' ')}
              fill={clusterColor(cluster)} fillOpacity="0.08" stroke={clusterColor(cluster)} strokeOpacity="0.35" strokeDasharray="6 5"/>
              : <rect key={cluster} x={Math.min(...members.map(item => item.x))-28}
                y={Math.min(...members.map(item => item.y))-28}
                width={Math.max(...members.map(item => item.x))-Math.min(...members.map(item => item.x))+56}
                height={Math.max(...members.map(item => item.y))-Math.min(...members.map(item => item.y))+56}
                rx="25" fill={clusterColor(cluster)} fillOpacity="0.07" stroke={clusterColor(cluster)} strokeOpacity="0.3" strokeDasharray="6 5"/>
          )}</g>
          <g className="editor-links">{editor.links.map(link => {
            const a=byId.get(link.source_id); const b=byId.get(link.target_id)
            if (!link.visible || !a?.visible || !b?.visible) return null
            const key=linkKey(link)
            return <g key={key}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={link.color}
                strokeWidth={link.width} opacity="0.62"/>
            </g>
          })}</g>
          <g className="editor-nodes">{editor.items.filter(item => item.visible).map(item =>
            <circle key={item.id} data-node={item.id} cx={item.x} cy={item.y} r={item.size}
              fill={item.color} fillOpacity="0.86" stroke={selected.includes(item.id) ? '#1e3944' : '#fff'}
              strokeWidth={selected.includes(item.id) ? 2.8 : 1.4}/>)}</g>
          <g className="editor-labels">{editor.items.filter(item => item.visible && item.labelVisible).map(item =>
            <text key={item.id} x={item.x+item.labelDx} y={item.y+item.labelDy}
              textAnchor="middle" fontSize="12" fill="#263b43">{item.label}</text>)}</g>
          {marquee && <rect x={marquee.x} y={marquee.y} width={marquee.width} height={marquee.height}
            fill="#62bbc4" fillOpacity="0.13" stroke="#238993" strokeDasharray="4 4"/>}
        </svg>
        <div className="editor-hint">{localStatus}</div>
      </div>
    </div>
  </div>
}
