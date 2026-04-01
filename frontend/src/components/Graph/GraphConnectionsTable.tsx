import { Typography } from '@neo4j-ndl/react';
import { ExtendedNode, ExtendedRelationship, Scheme } from '../../types';
import { LegendsChip } from './LegendsChip';

interface GraphConnectionsTableProps {
  nodes: ExtendedNode[];
  relationships: ExtendedRelationship[];
  scheme: Scheme;
}

const GraphConnectionsTable = ({ nodes, relationships, scheme }: GraphConnectionsTableProps): JSX.Element | null => {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  const rows = relationships
    .map((rel) => ({ rel, source: nodeById.get(rel.from), target: nodeById.get(rel.to) }))
    .filter(
      (r): r is { rel: ExtendedRelationship; source: ExtendedNode; target: ExtendedNode } =>
        r.source !== undefined && r.target !== undefined
    );

  if (rows.length === 0) {
    return null;
  }

  return (
    <div className='px-4 mt-2'>
      <Typography variant='subheading-small' className='mb-2'>
        Connections ({rows.length})
      </Typography>
      <div className='overflow-x-auto'>
        <table className='w-full text-xs border-collapse'>
          <thead>
            <tr className='border-b border-palette-neutral-border-weak'>
              <th className='text-left py-2 pr-3 font-medium whitespace-nowrap'>Source</th>
              <th className='text-left py-2 pr-3 font-medium whitespace-nowrap'>Source Type</th>
              <th className='text-left py-2 pr-3 font-medium whitespace-nowrap'>Relationship</th>
              <th className='text-left py-2 pr-3 font-medium whitespace-nowrap'>Target</th>
              <th className='text-left py-2 font-medium whitespace-nowrap'>Target Type</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ rel, source, target }) => (
              <tr key={rel.id} className='border-b border-palette-neutral-border-weak hover:bg-palette-neutral-bg-weak'>
                <td className='py-1.5 pr-3 max-w-[120px]'>
                  <span className='block truncate' title={source.caption ?? source.id}>
                    {source.caption || source.id}
                  </span>
                </td>
                <td className='py-1.5 pr-3'>
                  <div className='flex gap-1 flex-wrap'>
                    {source.labels.map((l) => (
                      <LegendsChip key={l} type='node' label={l} scheme={scheme} />
                    ))}
                  </div>
                </td>
                <td className='py-1.5 pr-3'>
                  <LegendsChip type='relationship' label={rel.caption ?? rel.type ?? ''} scheme={{}} />
                </td>
                <td className='py-1.5 pr-3 max-w-[120px]'>
                  <span className='block truncate' title={target.caption ?? target.id}>
                    {target.caption || target.id}
                  </span>
                </td>
                <td className='py-1.5'>
                  <div className='flex gap-1 flex-wrap'>
                    {target.labels.map((l) => (
                      <LegendsChip key={l} type='node' label={l} scheme={scheme} />
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default GraphConnectionsTable;
