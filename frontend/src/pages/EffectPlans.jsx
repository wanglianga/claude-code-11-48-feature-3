import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

export const DEFAULT_SPEC = {
  cohort_conditions: [
    { cid: 'inc-dx', kind: 'include', variable: 'diagnosis.code', op: 'in', value: ['RD01', 'RD02', 'RD03'] },
  ],
  treatment_variable: 'treatment.path',
  treated: 'A',
  control: 'B',
  outcome_variable: 'followup.outcome',
  outcome_value: 'remission',
  covariates: ['patient.sex', 'patient.birth_year'],
  covariate_types: { 'patient.sex': 'categorical', 'patient.birth_year': 'numeric' },
  measures: ['risk_difference', 'log_odds_ratio_adjusted'],
  primary_measure: 'risk_difference',
  primary_pooling: 'fixed',
}

const STATUS_LABEL = {
  draft: '草稿', in_review: '方法学审查中', approved: '已批准', rejected: '已拒绝',
}

export default function EffectPlans() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [plans, setPlans] = useState([])
  const [queue, setQueue] = useState([])
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')

  async function reload() {
    setPlans(await api.get('/api/effect-plans'))
    if (user.role === 'reviewer') {
      setQueue(await api.get('/api/effect-plans/review/queue'))
    }
  }
  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function create() {
    setMsg('')
    try {
      const p = await api.post('/api/effect-plans', { name: name || '未命名效应方案', spec: DEFAULT_SPEC })
      setName('')
      navigate(`/effect-plans/${p.id}`)
    } catch (e) {
      setMsg(`创建失败：${e.message}`)
    }
  }

  return (
    <>
      {user.role === 'researcher' && (
        <div className="panel">
          <h2>新建研究方案（效应汇总）</h2>
          {msg && <div className="alert bad">{msg}</div>}
          <p className="small muted">
            方案在运行前固定：共同队列规则、治疗比较、结局、调整协变量，以及以固定效应为主还是随机效应为主。
            创建后可在详情页编辑规格并提交方法学审查。
          </p>
          <div className="row">
            <input className="grow" placeholder="方案名称" value={name}
              onChange={(e) => setName(e.target.value)} />
            <button onClick={create}>创建草稿</button>
          </div>
        </div>
      )}

      {user.role === 'reviewer' && queue.length > 0 && (
        <div className="panel">
          <h2>待审方案版本（方法学审批）</h2>
          <table className="grid">
            <thead><tr><th>方案</th><th>版本</th><th>主方法</th><th>提交人</th><th>提交时间</th><th></th></tr></thead>
            <tbody>
              {queue.map((v) => (
                <tr key={v.id}>
                  <td>方案 #{v.plan_id}</td>
                  <td>v{v.version_no}</td>
                  <td><span className="tag info">{v.spec.primary_pooling === 'random' ? '随机效应为主' : '固定效应为主'}</span></td>
                  <td>{v.created_by}</td>
                  <td className="small">{v.submitted_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td><Link to={`/effect-plans/${v.plan_id}`}>审查</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel">
        <h2>研究方案列表</h2>
        {msg && user.role !== 'researcher' && <div className="alert bad">{msg}</div>}
        <table className="grid">
          <thead><tr><th>ID</th><th>名称</th><th>当前版本</th><th>已批准版本</th><th></th></tr></thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.id}>
                <td className="mono">#{p.id}</td>
                <td>{p.name}</td>
                <td>
                  {p.current_version_no ? `v${p.current_version_no}` : '—'}{' '}
                  <span className={`tag ${p.current_status === 'approved' ? 'ok' : p.current_status === 'rejected' ? 'bad' : 'warn'}`}>
                    {STATUS_LABEL[p.current_status] || p.current_status}
                  </span>
                </td>
                <td>{p.approved_version_no ? `v${p.approved_version_no}` : '—'}</td>
                <td><Link to={`/effect-plans/${p.id}`}>打开</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
