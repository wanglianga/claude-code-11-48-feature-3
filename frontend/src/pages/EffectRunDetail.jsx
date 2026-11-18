import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import EffectResult, { StatusTag } from '../components/EffectResult'

export default function EffectRunDetail() {
  const { id } = useParams()
  const { user } = useAuth()
  const [run, setRun] = useState(null)
  const [outputs, setOutputs] = useState([])
  const [title, setTitle] = useState('')
  const [msg, setMsg] = useState('')

  async function reload() {
    const r = await api.get(`/api/effect-runs/${id}`)
    setRun(r)
    const os = await api.get('/api/effect-outputs')
    setOutputs(os.filter((o) => o.run_id === r.id))
  }
  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function createOutput() {
    setMsg('')
    try {
      const o = await api.post(`/api/effect-runs/${id}/outputs`, {
        title: title || `效应运行 #${id} 汇总输出`,
      })
      setMsg(`输出 #${o.id} 已提交审查`)
      await reload()
    } catch (e) {
      setMsg(`创建输出失败：${e.message}`)
    }
  }

  if (!run) return <div className="panel">加载中…</div>
  const spec = run.spec

  return (
    <>
      <div className="panel">
        <h2>效应运行 #{run.id}（汇总版本 v{run.pool_version}）</h2>
        {msg && <div className="alert">{msg}</div>}
        <p className="small muted">
          方案#{run.plan_id} · 方案版本 v{run.plan_version_no} · 规格哈希{' '}
          <span className="mono">{run.spec_hash?.slice(0, 16)}…</span> · k={run.k_threshold}
        </p>
        <p className="small">
          比较：{spec.treatment_variable} <b>{spec.treated}</b> vs <b>{spec.control}</b> ·
          结局：{spec.outcome_variable}={spec.outcome_value} · 协变量：
          {(spec.covariates || []).join('、') || '无'}
        </p>
        <h3>批次快照（启动时锁定；中心修正批次只会形成新的汇总版本）</h3>
        <table className="grid">
          <thead>
            <tr><th>中心</th><th>批次</th><th>本地状态</th><th>纳入/排除</th><th>效应量（主）</th><th>收敛</th><th>max|SMD|</th><th>说明</th></tr>
          </thead>
          <tbody>
            {run.center_results.map((c) => {
              const d = c.diagnostics
              const eff = d?.effects?.[run.primary_measure]
              return (
                <tr key={c.center_code}>
                  <td>{c.center_code} · {c.center_name}</td>
                  <td className="mono">#{c.batch_id}（{c.batch_label}）</td>
                  <td><StatusTag status={c.status} /></td>
                  <td>{d ? `${d.n_included} / ${d.n_excluded}` : '—'}</td>
                  <td className="mono">{eff ? `${eff.theta}（${eff.se}）` : '—（无值传出）'}</td>
                  <td>{d ? `收敛(${d.model?.iterations}迭代)` : '—'}</td>
                  <td>{d?.balance ? d.balance.max_abs_smd : '—'}</td>
                  <td className="small">{c.status_reason || c.error || ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h3>协调端汇总（仅使用通过最小单元格检查的聚合诊断）</h3>
        <EffectResult payload={run.result} />
        {user.role === 'researcher' && run.status.startsWith('completed') && (
          <div className="row" style={{ marginTop: 12 }}>
            <input className="grow" placeholder="输出标题" value={title}
              onChange={(e) => setTitle(e.target.value)} />
            <button onClick={createOutput}>提交汇总输出审查</button>
          </div>
        )}
      </div>

      {outputs.length > 0 && (
        <div className="panel">
          <h3>本运行的汇总输出</h3>
          <table className="grid">
            <thead><tr><th>ID</th><th>标题</th><th>状态</th><th>标记</th><th>创建时间</th></tr></thead>
            <tbody>
              {outputs.map((o) => (
                <tr key={o.id}>
                  <td className="mono">#{o.id}</td>
                  <td>{o.title}</td>
                  <td>
                    <span className={`tag ${o.status === 'approved' ? 'ok' : o.status === 'rejected' ? 'bad' : 'warn'}`}>
                      {o.status}
                    </span>
                  </td>
                  <td>{o.flag_count}</td>
                  <td className="small">{o.created_at?.slice(0, 19).replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
