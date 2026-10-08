export const parameterLabels = {
  Attraction: 'attraction', 吸引力: 'attraction',
  Repulsion: 'repulsion', 斥力: 'repulsion',
  Resolution: 'resolution', 聚类分辨率: 'resolution',
  'Minimum cluster size': 'min_cluster_size', 最小聚类大小: 'min_cluster_size',
  'Merge small clusters': 'merge_small_clusters', 合并小聚类: 'merge_small_clusters',
  'Maximum label length': 'max_label_length', 标签最大长度: 'max_label_length',
  'Minimum strength': 'min_link_strength', 最小连线强度: 'min_link_strength',
  'Maximum links': 'max_n_links', 最多连线数: 'max_n_links',
  'Colored links': 'colored_links', 彩色连线: 'colored_links',
  'Curved links': 'curved_links', 弯曲连线: 'curved_links',
  Scale: 'scale', 缩放: 'scale'
}

export function mergeSavedParameters(payload, edits) {
  const current = JSON.parse(payload)
  current.config ||= {}
  current.config.parameters = {...current.config.parameters, ...edits}
  return current
}
