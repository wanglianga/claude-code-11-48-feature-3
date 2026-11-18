import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

const RUN_STATUS = {
  pending: ['排队中', ''], running: ['运行中', ''],
  completed: ['完成', 'ok'], completed_with_gaps: ['完成（有未纳入中心）', 'warn'],
  failed: ['失败', 'bad'],
}

export default function EffectRuns() {
  const [runs, setRuns] = useState([])
  const [plans, setPlans] = useState([])
  const [planId, setPlanId] = useState('')
  const [msg, setMsg] = useState('')

  async function reload() {
    const [rs, ps] = await Promise.all([api.get('/api/effect-runs'), api.get('/api/effect-plans')])
    setRuns(rs)
    setPlans(ps.filter((p) => p.approved_version_id))
  }
  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
  }, [])

  async function createRun() {
    setMsg('')
    const plan = plans.find((p) => String(p.id) === String(planId))
    if (!plan) return
    try {
      const run = await api.post('/api/effect-runs', { plan_version_id: plan.approved_version_id })
      setMsg(`效应运行 #${run.id}（汇总版本 v${run.pool_version}）已创建：${run.status}`)
      await reload()
    } catch (e) {
      setMsg(`创建失败：${e.message}`)
    }
  }

  return (
    <>
      <div className="panel">
        <h2>新建效应运行</h2>
        {msg && <div className="alert">{msg}</div>}
        <div className="row">
          <label className="field grow">
            研究方案（仅已获方法学批准的版本可运行；运行即形成新的汇总版本）
            <select value={planId} onChange={(e) => setPlanId(e.target.value)}>
              <option value="">选择方案</option>
              {plans.map((p) => (
                <option key={p.id} value={p.id}>{p.name}（已批准 v{p.approved_version_no}）</option>
              ))}
            </select>
          </label>
          <button disabled={!planId} onClick={createRun}>启动效应运行</button>
        </div>
      </div>

      <div className="panel">
        <h2>效应运行列表</h2>
        <table className="grid">
          <thead>
            <tr><th>ID</th><th>方案</th><th>汇总版本</th><th>主方法</th><th>状态</th><th>纳入中心</th><th>创建时间</th><th></th></tr>
          </thead>
          <tbody>
            {runs.map((r) => {
              const [label, cls] = RUN_STATUS[r.status] || [r.status, '']
              return (
                <tr key={r.id}>
                  <td className="mono">#{r.id}</td>
                  <td>方案#{r.plan_id} · v{r.plan_version_no}</td>
                  <td><span className="tag info">v{r.pool_version}</span></td>
                  <td>{r.primary_pooling === 'random' ? '随机效应' : '固定效应'}</td>
                  <td><span className={`tag ${cls}`}>{label}</span></td>
                  <td>
                    {r.centers_included}/{r.centers_total} 纳入
                    {r.centers_included < r.centers_total && (
                      <span className="tag warn" style={{ marginLeft: 6 }}>
                        {r.centers_total - r.centers_included} 未纳入
                      </span>
                    )}
                  </td>
                  <td className="small">{r.created_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td><Link to={`/effect-runs/${r.id}`}>详情</Link></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}
