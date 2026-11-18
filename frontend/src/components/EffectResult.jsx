const POOLING_LABEL = { fixed: '固定效应为主', random: '随机效应为主' }
const SEV_CLS = { info: 'info', warn: 'warn', bad: 'bad' }

export const CENTER_STATUS_LABEL = {
  included: ['纳入汇总', 'ok'],
  insufficient_events: ['事件过少（保护）', 'bad'],
  small_cell_suppressed: ['小单元格抑制（保护）', 'bad'],
  no_common_support: ['无共同支持（保护）', 'bad'],
  model_not_converged: ['模型不收敛（保护）', 'bad'],
  missing_mapping: ['缺变量映射', 'bad'],
  failed: ['执行失败', 'bad'],
  suppressed_review: ['审查抑制（保护）', 'bad'],
  pending: ['等待', ''],
}

export function StatusTag({ status }) {
  const [label, cls] = CENTER_STATUS_LABEL[status] || [status, '']
  return <span className={`tag ${cls}`}>{label}</span>
}

export function Hints({ hints = [] }) {
  if (!hints.length) return null
  return (
    <>
      <h3>异质性与稳定性提示</h3>
      <ul style={{ margin: '4px 0', paddingLeft: 18 }}>
        {hints.map((h, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            <span className={`tag ${SEV_CLS[h.severity] || ''}`}>{h.type}</span>{' '}
            <span className="small">{h.text}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

export function Premises({ premises }) {
  if (!premises) return null
  return (
    <div className="alert" style={{ borderColor: 'var(--line)', background: '#f8fafc' }}>
      <b>方法学选择（方案运行前确定，非自动正确答案）：</b>
      <span className="tag info" style={{ marginLeft: 6 }}>
        {POOLING_LABEL[premises.primary_pooling] || premises.primary_pooling}
      </span>
      <div className="small" style={{ marginTop: 6 }}>固定效应前提：{premises.fixed}</div>
      <div className="small" style={{ marginTop: 2 }}>随机效应前提：{premises.random}</div>
    </div>
  )
}

export function PoolingTable({ pooling, pooledSuppressed = false }) {
  if (!pooling) return null
  const measures = Object.entries(pooling.measures || {})
  if (pooledSuppressed) {
    return <div className="alert bad">汇总估计已被审查抑制（⊘），仅保留中心层信息与保护类别。</div>
  }
  if (pooling.k_included === 0) {
    return <div className="alert bad">没有中心通过本地检查，无法形成汇总估计（不以零效应补位）。</div>
  }
  return (
    <table className="grid">
      <thead>
        <tr>
          <th>效应量</th>
          <th>固定效应 θ（SE）[95%CI]</th>
          <th>随机效应 θ（SE）[95%CI] · τ²</th>
          <th>异质性 Q(df) / I² / p</th>
        </tr>
      </thead>
      <tbody>
        {measures.map(([m, d]) => (
          <tr key={m}>
            <td>
              {d.label}{' '}
              {m === pooling.measure_primary && <span className="tag info">主效应量</span>}
            </td>
            <td className="mono">
              {d.fixed.theta}（{d.fixed.se}）[{d.fixed.ci[0]}, {d.fixed.ci[1]}]
            </td>
            <td className="mono">
              {d.random.theta}（{d.random.se}）[{d.random.ci[0]}, {d.random.ci[1]}]
              {' '}τ²={d.random.tau2}
            </td>
            <td className="mono">
              {d.heterogeneity.Q}（{d.heterogeneity.df}）/ {d.heterogeneity.I2}% /{' '}
              {d.heterogeneity.p}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function Contributions({ pooling, suppressed = [] }) {
  if (!pooling || pooling.k_included === 0) return null
  const primary = pooling.measures?.[pooling.measure_primary]
  if (!primary) return null
  const sup = new Set(suppressed)
  const rows = (primary.contributions || []).filter((c) => !sup.has(c.center))
  const range = primary.contribution_range
  return (
    <>
      <h3>中心贡献（主效应量 · 按 θ 排序显示范围）</h3>
      {range && !sup.has(range.min.center) && !sup.has(range.max.center) && (
        <p className="small muted">
          贡献范围：[{range.min.center} {range.min.theta}, {range.max.center} {range.max.theta}]
        </p>
      )}
      <table className="grid">
        <thead>
          <tr><th>中心</th><th>θ（SE）</th><th>纳入例数</th><th>固定效应权重</th><th>随机效应权重</th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.center}>
              <td>{c.center}</td>
              <td className="mono">{c.theta}（{c.se}）</td>
              <td>{c.n_included}</td>
              <td>{(c.weight_fe * 100).toFixed(1)}%</td>
              <td>{(c.weight_re * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function ProtectedList({ protected_ = [] }) {
  if (!protected_.length) return null
  return (
    <>
      <h3>未纳入中心（保护类别 / 覆盖缺口，不以零效应补位）</h3>
      <table className="grid">
        <thead><tr><th>中心</th><th>类别</th><th>说明</th></tr></thead>
        <tbody>
          {protected_.map((p) => (
            <tr key={p.center}>
              <td>{p.center}</td>
              <td><StatusTag status={p.status} /></td>
              <td className="small">{p.reason || p.error || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function SupportRange({ supportRange }) {
  const entries = Object.entries(supportRange || {})
  if (!entries.length) return null
  return (
    <>
      <h3>可比支持范围（所有纳入中心两臂均有代表的协变量区域）</h3>
      <table className="grid">
        <thead><tr><th>协变量</th><th>可比范围</th></tr></thead>
        <tbody>
          {entries.map(([cv, s]) => (
            <tr key={cv}>
              <td className="mono">{cv}</td>
              <td>
                {s.type === 'numeric'
                  ? (s.range ? `${s.range[0]} ~ ${s.range[1]}` : `无共同范围（${s.note}）`)
                  : `共有水平：${(s.levels || []).join(' / ') || '无'}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export default function EffectResult({ payload }) {
  if (!payload) return null
  const pooling = payload.pooling
  return (
    <>
      <Premises premises={payload.premises || pooling?.premises} />
      <PoolingTable pooling={pooling} pooledSuppressed={payload.pooled_suppressed} />
      <Hints hints={pooling?.hints} />
      <Contributions pooling={pooling} suppressed={payload.suppressed_centers} />
      <SupportRange supportRange={payload.support_range || pooling?.support_range} />
      <ProtectedList protected_={payload.protected} />
    </>
  )
}
