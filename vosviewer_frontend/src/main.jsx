import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { VOSviewerOnline } from 'vosviewer-online'
import { mergeSavedParameters, parameterLabels } from './persistence.mjs'
import { savedViewMode, withTimePalette, yearExtent } from './time_view.mjs'
import NetworkEditor from './NetworkEditor.jsx'
import './style.css'

const words = {
  File:'文件', Save:'保存', Screenshot:'截图', View:'视图', Find:'查找', Update:'更新',
  Layout:'布局', Clustering:'聚类', Attraction:'吸引力', Repulsion:'斥力', Resolution:'聚类分辨率',
  Visualization:'可视化', 'Network Visualization':'网络视图', 'Overlay Visualization':'叠加视图',
  'Density Visualization':'密度视图', Labels:'标签', Scale:'缩放', Weights:'权重', Scores:'得分',
  Items:'节点', Links:'连线', Clusters:'聚类', Colors:'颜色', Background:'背景', Lines:'连线',
  Size:'大小', Color:'颜色', 'Size variation':'大小变化', 'Maximum label length':'标签最大长度',
  'Minimum strength':'最小连线强度', 'Maximum links':'最多连线数', 'Curved links':'弯曲连线',
  'Color schemes':'配色方案', 'Cluster colors':'聚类颜色', 'Score colors':'得分颜色',
  'Total link strength':'总连线强度', Show:'显示', Hide:'隐藏', Reset:'重置', Close:'关闭',
  'Rotate / flip':'旋转与翻转', 'Degrees to rotate':'旋转角度', Rotate:'旋转',
  'Flip horizontally':'水平翻转', 'Flip vertically':'垂直翻转',
  Normalization:'标准化', 'Normalization method':'标准化方法',
  'Association strength':'关联强度', 'Advanced parameters':'高级参数',
  'Update layout':'更新布局', 'Minimum cluster size':'最小聚类大小',
  'Merge small clusters':'合并小聚类', 'Update clustering':'更新聚类',
  Documents:'文献数', Citations:'引用次数', Apply:'应用', Cancel:'取消',
  Advanced:'高级', Search:'搜索'
}

function localize(root) {
  if (!root) return
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let node
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('input, textarea, canvas, svg')) continue
    const value = node.nodeValue.trim()
    if (words[value]) node.nodeValue = node.nodeValue.replace(value, words[value])
    else {
      const match = value.match(/^(Items|Links|Clusters|Total link strength):\s*(\d+)$/)
      if (match) node.nodeValue = node.nodeValue.replace(value, `${words[match[1]]}: ${match[2]}`)
    }
  }
  root.querySelectorAll('[title], [aria-label], [placeholder]').forEach((element) => {
    for (const attr of ['title', 'aria-label', 'placeholder']) {
      const value = element.getAttribute(attr)
      if (words[value]) element.setAttribute(attr, words[value])
    }
  })
}

function nativeButton(icon) { return document.querySelector(`svg[data-testid="${icon}Icon"]`)?.closest('button') }

const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve))
async function waitFor(find, frames = 30) {
  for (let index = 0; index < frames; index += 1) {
    const value = find()
    if (value) return value
    await nextFrame()
  }
  return null
}

async function selectNativeNodeColor(host, mode) {
  const tabs = [...host.querySelectorAll('[role="tab"]')]
  const viewTab = tabs.find(tab => ['View', '视图'].includes(tab.textContent.trim()))
  if (!viewTab) throw new Error('VOS 控制面板尚未初始化')
  const previousTab = tabs.find(tab => tab.getAttribute('aria-selected') === 'true')
  try {
    viewTab.click()
    const form = await waitFor(() => [...host.querySelectorAll('.MuiFormControl-root')].find(element =>
      ['Color', '颜色'].includes(element.querySelector('label')?.textContent.trim())))
    const select = form?.querySelector('[role="button"], [role="combobox"]')
    if (!select) throw new Error('未找到 VOS 节点颜色选项')
    select.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, view: window }))
    const menu = await waitFor(() => [...document.querySelectorAll('[role="listbox"]')].at(-1))
    const options = [...(menu?.querySelectorAll('[role="option"]') || [])]
    const option = mode === 'network' ? options[0]
      : options.find(element => element.textContent.trim() === 'Avg. pub. year')
    if (!option) throw new Error('未找到平均发文年份颜色数据')
    option.click()
    await nextFrame()
  } finally {
    if (previousTab && previousTab !== viewTab) previousTab.click()
  }
}

function configureControlPanel(host, initialized) {
  const tabs = [...host.querySelectorAll('[role="tab"]')]
  const updateTab = tabs.find(tab => ['Update', '更新'].includes(tab.textContent.trim()))
  if (!updateTab) return false
  const panel = updateTab.closest('.MuiPaper-root')
  if (!panel) return false
  if (!initialized) {
    if (panel.className.includes('control-panel-closed')) {
      panel.querySelector('[class*="close-open-button"]')?.click()
    }
    updateTab.click()
  }
  const content = panel.querySelector('[class*="content-box"]')
  if (content && updateTab.getAttribute('aria-selected') === 'true') {
    const order = {'Layout': 1, '布局': 1, 'Clustering': 2, '聚类': 2,
      'Rotate / flip': 3, '旋转与翻转': 3, 'Normalization': 4, '标准化': 4}
    content.style.display = 'flex'
    content.style.flexDirection = 'column'
    let section = 5
    for (const child of content.children) {
      const heading = child.textContent?.trim()
      if (Object.hasOwn(order, heading)) section = order[heading]
      child.style.order = String(section)
      child.style.flexShrink = '0'
    }
  }
  return true
}

function App() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('图谱调整会自动保存')
  const [viewMode, setViewMode] = useState('network')
  const [viewerReady, setViewerReady] = useState(false)
  const [editorGraph, setEditorGraph] = useState(null)
  const pending = useRef(null)
  const inFlight = useRef(null)
  const parameterEdits = useRef({})
  const dirty = useRef(true)
  const revision = useRef(0)
  const saveDelay = useRef(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const value = await window.pywebview.api.load_data()
        if (active) { setViewMode(savedViewMode(value)); setData(withTimePalette(value)) }
      }
      catch (exc) { if (active) setError(`图谱读取失败：${exc}`) }
    }
    if (window.pywebview?.api) load()
    else window.addEventListener('pywebviewready', load, { once:true })
    return () => { active = false; window.removeEventListener('pywebviewready', load) }
  }, [])

  useEffect(() => {
    if (!data || editorGraph) return
    const host = document.getElementById('vos-component')
    let queued = false
    let initialized = false
    const observer = new MutationObserver(() => {
      if (queued) return
      queued = true
      requestAnimationFrame(() => {
        if (configureControlPanel(host, initialized)) {
          initialized = true
          setViewerReady(true)
        }
        localize(host)
        queued = false
      })
    })
    observer.observe(host, { childList:true, subtree:true, characterData:true, attributes:true,
      attributeFilter:['title','aria-label','placeholder'] })
    localize(host)
    return () => observer.disconnect()
  }, [data, editorGraph])

  useEffect(() => {
    if (!data || editorGraph) return
    const changed = event => {
      const insideViewer = document.querySelector('#vos-component .native-view')?.contains(event.target)
      const nativeMenu = event.target.closest?.('.MuiPopover-root, [role="listbox"]')
      if (!insideViewer && !nativeMenu) return
      dirty.current = true
      revision.current += 1
      if (saveDelay.current) clearTimeout(saveDelay.current)
      saveDelay.current = setTimeout(() => {
        if (!document.body.innerText.includes('Running ')) {
          saveCurrent().catch(exc => setStatus(`自动保存失败：${exc}`))
        }
      }, 900)
      const element = event.target
      const slider = element.closest?.('.MuiSlider-root')
      if (slider) {
        const label = slider.parentElement?.querySelector('.MuiTypography-root')?.textContent?.trim()
        const key = parameterLabels[label]
        const value = Number(slider.querySelector('[role="slider"]')?.getAttribute('aria-valuenow'))
        if (key && Number.isFinite(value)) parameterEdits.current[key] = value
      }
      if (!(element instanceof HTMLInputElement)) return
      const label = element.labels?.[0]?.textContent?.trim()
        || element.closest('.MuiFormControl-root')?.querySelector('label')?.textContent?.trim()
        || element.closest('.MuiFormControlLabel-root')?.textContent?.trim()
      const key = parameterLabels[label]
      if (!key) return
      if (element.type === 'checkbox') parameterEdits.current[key] = element.checked
      else if (element.value.trim() !== '' && Number.isFinite(Number(element.value))) {
        parameterEdits.current[key] = Number(element.value)
      }
    }
    document.addEventListener('input', changed, true)
    document.addEventListener('change', changed, true)
    document.addEventListener('pointerup', changed, true)
    return () => {
      if (saveDelay.current) clearTimeout(saveDelay.current)
      document.removeEventListener('input', changed, true)
      document.removeEventListener('change', changed, true)
      document.removeEventListener('pointerup', changed, true)
    }
  }, [data, editorGraph])

  useEffect(() => {
    if (!data || editorGraph) return
    const intercept = async (event) => {
      const anchor = event.target.closest?.('a[download]')
      if (!anchor || !document.querySelector('#vos-component .native-view')?.contains(anchor)) return
      event.preventDefault(); event.stopImmediatePropagation()
      try {
        const name = (anchor.download || '').toLowerCase()
        if (name.endsWith('.png')) {
          const result = await window.pywebview.api.save_png(anchor.href)
          setStatus(`截图已保存：${result.path}`)
        } else if (name.endsWith('.json')) {
          const payload = await (await fetch(anchor.href)).text()
          const current = mergeSavedParameters(payload, parameterEdits.current)
          const result = await window.pywebview.api.save_state(JSON.stringify(current))
          if (result.changed) setStatus('图谱调整已自动保存')
          pending.current?.resolve({...result, map: current})
          if (anchor.href.startsWith('blob:')) URL.revokeObjectURL(anchor.href)
        }
      } catch (exc) { setStatus(`保存失败：${exc}`); pending.current?.reject(exc) }
      finally { pending.current = null }
    }
    document.addEventListener('click', intercept, true)
    return () => document.removeEventListener('click', intercept, true)
  }, [data, editorGraph])

  const saveCurrent = async () => {
    if (inFlight.current) return inFlight.current
    const savingRevision = revision.current
    const button = nativeButton('Save')
    if (!button) throw new Error('图谱尚未初始化')
    const operation = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { pending.current = null; reject(new Error('未收到图谱数据')) }, 10000)
      pending.current = {
        resolve: value => { clearTimeout(timeout); resolve(value) },
        reject: reason => { clearTimeout(timeout); reject(reason) }
      }
      button.click()
    })
    inFlight.current = operation
    try {
      const result = await operation
      if (revision.current === savingRevision) dirty.current = false
      return result
    } finally { inFlight.current = null }
  }

  useEffect(() => {
    if (!data || editorGraph) return
    const timer = setInterval(() => {
      if (dirty.current && !document.body.innerText.includes('Running ')) {
        saveCurrent().catch(exc => setStatus(`自动保存失败：${exc}`))
      }
    }, 1500)
    return () => clearInterval(timer)
  }, [data, editorGraph])

  const exportMap = async () => {
    try {
      await saveCurrent()
      const result = await window.pywebview.api.export_map()
      setStatus(`已导出地图：${result.map}；网络：${result.network}`)
    } catch (exc) { setStatus(`导出失败：${exc}`) }
  }
  const screenshot = async () => {
    const button = nativeButton('PhotoCamera') || nativeButton('Screenshot')
    if (button) button.click()
    else setStatus('截图控件尚未就绪')
  }
  const changeView = async mode => {
    if (mode === viewMode) return
    if (mode === 'time' && !yearExtent(data)) {
      setStatus('当前图谱没有可用的发表年份，无法按时间着色')
      return
    }
    try {
      await selectNativeNodeColor(document.getElementById('vos-component'), mode)
      setViewMode(mode)
      dirty.current = true
      revision.current += 1
      setStatus(mode === 'time' ? '节点已按平均发文年份着色；布局和连线保持不变' : '已恢复网络聚类配色')
      await saveCurrent()
    } catch (exc) { setStatus(`切换视图失败：${exc}`) }
  }

  const openEditor = async () => {
    try {
      await saveCurrent()
      // Native VOS JSON export omits our manual styling metadata. Reload the
      // backend's merged graph so repeated editor sessions retain those edits.
      setEditorGraph(await window.pywebview.api.load_data())
      setStatus('网络图编辑模式已打开；更改会自动保存到当前图谱')
    } catch (exc) { setStatus(`无法打开编辑模式：${exc}`) }
  }
  const closeEditor = saved => {
    parameterEdits.current = {}
    dirty.current = false
    setViewerReady(false)
    setEditorGraph(null)
    setViewMode(savedViewMode(saved))
    setData(withTimePalette(saved))
    setStatus('手工编辑已保存；可再次进入编辑模式继续修改')
  }

  if (error) return <div className="message error">{error}</div>
  if (!data) return <div className="message">正在加载 VOS 图谱…</div>
  if (editorGraph) return <NetworkEditor graph={editorGraph} onClose={closeEditor} onStatus={setStatus}/>
  return <div className="mannul-shell">
    <div className="mannul-toolbar"><strong>VOS 图谱</strong>
      <div className="view-switch" role="group" aria-label="图谱视图">
        <button className={viewMode === 'network' ? 'selected' : ''} disabled={!viewerReady}
          aria-pressed={viewMode === 'network'} onClick={() => changeView('network')}>网络视图</button>
        <button className={viewMode === 'time' ? 'selected' : ''}
          disabled={!viewerReady || !yearExtent(data)}
          title={!yearExtent(data) ? '当前图谱缺少发表年份' : '按节点平均发文年份着色'}
          aria-pressed={viewMode === 'time'} onClick={() => changeView('time')}>时间视图</button>
      </div>
      <span>可调整布局、斥力、吸引力与聚类分辨率</span>
      <button onClick={openEditor} disabled={!viewerReady || viewMode !== 'network'}
        title={viewMode !== 'network' ? '请先切回网络视图' : '编辑节点、连线和聚类'}>编辑网络图</button>
      <button onClick={() => saveCurrent().catch(exc => setStatus(`保存失败：${exc}`))}>保存 JSON</button>
      <button onClick={exportMap}>导出 VOS 地图</button><button onClick={screenshot}>截图 PNG</button></div>
    <div id="vos-component"><div className="native-view"><VOSviewerOnline data={data} /></div></div>
    <div className="mannul-status" title={status}>{status}</div>
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
