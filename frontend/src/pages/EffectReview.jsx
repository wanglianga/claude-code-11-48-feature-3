import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

export default function EffectReview() {
  const [outputs, setOutputs] = useState([])
  const [msg, setMsg] = useState('')

  useEffect(() => {
    api.get('/api/effect-outputs').then(setOutputs).catch((e) => setMsg(e.message))
  }, [])

  return (
    <div className="panel">
      <h2>效应汇总审查队列</h2>
      {msg && <div className="alert bad">{msg}</div>}
      {outputs.length === 0 ? <p className="muted">暂无效应汇总输出。</p> : (
        <table className="grid">
          <thead><tr><th>ID</th><th>标题</th><th>效应运行</th><th>状态</th><th>差分标记</th><th>提交人</th><th></th></tr></thead>
          <tbody>
            {outputs.map((o) => (
              <tr key={o.id}>
                <td className="mono">#{o.id}</td>
                <td>{o.title}</td>
                <td className="mono">#{o.run_id}</td>
                <td>
                  <span className={`tag ${o.status === 'approved' ? 'ok' : o.status === 'rejected' ? 'bad' : 'warn'}`}>
                    {o.status}
                  </span>
                </td>
                <td>{o.flag_count > 0 ? <span className="tag bad">{o.flag_count}</span> : '0'}</td>
                <td>{o.created_by}</td>
                <td><Link to={`/effect-review/${o.id}`}>审查</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
