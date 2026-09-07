"""Synthetic LD input buses and whole-expression operators through real readers."""
import itertools
from gx3cli.review_gx3_project import LadderRow
from gx3cli.gx3_ladder_logic import (
    enable_logic_for_output, output_elements_for, logic_to_text,
    condition_refs_from_logic, logic_stats,
)
from gx3cli.gx3_live_read import evaluate_logic
from gx3cli.gx3_matiec_export import logic_to_st, StBuildContext


def element(n,x,y,kind='ct',ct='a'):
    return ('e{s=ce{op='+kind+'{op=#:ct='+ct+':as=[as{vt=Abl}]}:args=['
            +'d{s=#:a='+str(n)+':vt=nn}]}:pos='+str(x)+','+str(y)+'}')


def row(tokens,elements,verticals='',dim='5x3'):
    data=('V1:'+str(len(tokens))+':'+':'.join(str(len(t)) for t in tokens)+':'
          +':'.join(tokens)+':cb{fg=fg{dim='+dim+':es=['+':'.join(elements)+']'
          +(':vs=['+verticals+']' if verticals else '')+'}}')
    return LadderRow('test_LDDB.db',1,'synthetic','',0,3,data,dim,[],'exact')


def condition(r,dev):
    return enable_logic_for_output(r,output_elements_for(r,dev)[0])


def test_all_contacts_on_an_input_bus_reach_all_outputs():
    r=row(['a','M','c','M']*3,
          [e for y in range(3) for e in [element(10+y,0,y),element(20+y,1,y,'cl')]],
          'v{pos=1,1}:v{pos=1,2}')
    trees=[condition(r,'M'+str(20+y)) for y in range(3)]
    for bits in itertools.product([False,True],repeat=3):
        values={'M'+str(10+i):value for i,value in enumerate(bits)}
        for tree in trees:
            assert evaluate_logic(tree,values)[0]==('pass' if any(bits) else 'block')
            assert {r['device'] for r in condition_refs_from_logic(tree)}=={'M10','M11','M12'}


def expression_row(opcode,ct='a',first_type='M',first_number=10):
    unary='e{s=ce{op=ct{op=#:ct='+ct+':as=[]}:args=[]}:pos=1,0}'
    return row(['a',first_type,'a','M',opcode,'a','M','c','M'],
               [element(first_number,0,0),element(11,0,1),unary,
                element(12,2,0),element(20,3,0,'cl')], 'v{pos=1,1}')


def test_inv_negates_the_entire_input_not_an_extra_contact():
    tree=condition(expression_row('INV'),'M20')
    for a,b,c in itertools.product([False,True],repeat=3):
        actual,_=evaluate_logic(tree,{'M10':a,'M11':b,'M12':c})
        assert actual==('pass' if (not (a or b)) and c else 'block')
    text=logic_to_text(tree)
    assert 'INV(' in text and 'OR' in text and 'M12' in text,text
    assert 'NOT' in logic_to_st(tree,StBuildContext())
    assert {ref['device'] for ref in condition_refs_from_logic(tree)}=={'M10','M11','M12'}


def test_edges_keep_their_input_and_are_unknown_in_one_snapshot():
    for ct,opcode in [('p','MEP'),('f','MEF')]:
        tree=condition(expression_row('ME',ct),'M20')
        pred=next(arg for arg in tree['args'] if arg.get('expression_operator'))
        assert pred['opcode']==opcode and pred['requires_previous_scan']
        assert pred['args'][0]['op']=='or'
        assert logic_stats(tree)['contacts']==3
        state,leaves=evaluate_logic(tree,{'M10':False,'M11':False,'M12':True})
        assert state=='unknown' and 'previous-scan' in leaves[0]['reason'],leaves
        assert opcode+'(' in logic_to_text(tree)
        ctx=StBuildContext();logic_to_st(tree,ctx)
        assert any(opcode in value for value in ctx.predicate_comments.values())


def test_false_input_is_not_skipped_before_inv_or_falling_edge():
    for opcode,ct in [('INV','a'),('ME','f')]:
        unary='e{s=ce{op=ct{op=#:ct='+ct+':as=[]}:args=[]}:pos=1,0}'
        r=row(['a','SM',opcode,'c','M'],[element(401,0,0),unary,element(20,2,0,'cl')])
        tree=condition(r,'M20')
        assert tree['args'][0]['op']=='false'
        assert evaluate_logic(tree,{})[0]==('pass' if opcode=='INV' else 'unknown')


def test_parallel_arm_operator_excludes_common_input_before_split():
    for opcode,ct in [('INV','a'),('ME','p'),('ME','f')]:
        unary='e{s=ce{op=ct{op=#:ct='+ct+'}:args=[]}:pos=2,0}'
        r=row(['a','M','a','M',opcode,'a','M','c','M'],
              [element(10,0,0),element(11,1,0),unary,element(12,1,1),
               'e{s=wire:pos=2,1}',element(20,3,0,'cl')],
              'v{pos=1,1}:v{pos=3,1}')
        tree=condition(r,'M20')
        def find_operator(node):
            if node.get('expression_operator'):return node
            for child in node.get('args',[]):
                found=find_operator(child)
                if found:return found
        operator=find_operator(tree)
        assert {ref['device'] for ref in condition_refs_from_logic(operator['args'][0])}=={'M11'},tree
        if opcode=='INV':
            for a,b,c in itertools.product([False,True],repeat=3):
                actual,_=evaluate_logic(tree,{'M10':a,'M11':b,'M12':c})
                assert actual==('pass' if a and ((not b) or c) else 'block')


def test_fanout_to_another_output_does_not_create_a_parallel_arm_scope():
    unary='e{s=ce{op=ct{op=#:ct=a}:args=[]}:pos=2,0}'
    r=row(['a','M','a','M','INV','c','M','a','M','c','M'],
          [element(10,0,0),element(11,1,0),unary,element(20,3,0,'cl'),
           element(12,1,1),element(21,2,1,'cl')], 'v{pos=1,1}')
    tree=condition(r,'M20')
    assert tree['expression_operator'] and tree['args'][0]['op']=='and',tree
    assert evaluate_logic(tree,{'M10':False,'M11':True})[0]=='pass'


def test_trace_and_dependency_consumers_keep_expression_inputs_and_state_gap():
    import tempfile
    from pathlib import Path
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_trace_state import build_trace
    from gx3cli.gx3_dependency_flow import dependency_refs_for_output
    r=expression_row('ME','p')
    output=output_elements_for(r,'M20')[0]
    refs=dependency_refs_for_output(r,output)
    assert {ref.device for _element,ref in refs}=={'M10','M11','M12'}
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)/'project'
        write_program(root,[('_guid/synthetic',r.data)])
        trace=build_trace(root,'M20',1,30,True,True)
        driver=trace['driver_rows'][0]
        assert any(item.get('opcode')=='MEP' for item in driver['temporal_predicates']),driver
        assert {c['device'] for c in driver['conditions']}=={'M10','M11','M12'}
        assert trace['analysis']['state']!='checked',trace['analysis']


def test_predicate_tree_keeps_width_literals_and_operand_order():
    from test_gx3_display_fidelity import row as predicate_row, dev
    from gx3cli.gx3_ladder_logic import positioned_elements, element_condition_logic
    for r,expected in [
        (predicate_row('<',['D','K_2'],['A32s','A32s'],[dev(10),'c{s=#:v=2}'],'ct'), '[D< D10 K2]'),
        (predicate_row('=',['String','HELLO','"HELLO"','D'],['Ass','Ass'],['c{s=#:v=#:t=#}',dev(10)],'ct'), '[$= "HELLO" D10]'),
    ]:
        element=positioned_elements(r)[0]
        assert logic_to_text(element_condition_logic(element))==expected
    r=predicate_row('=',['D','D'],['A16s','A16s'],[dev(10),dev(10)],'ct')
    element=positioned_elements(r)[0]
    assert element.end_x==3,element
    assert element.operands==['D10','D10']


def test_pulse_contacts_are_not_folded_to_level_constants():
    from gx3cli.gx3_dead_logic import ConstantFact, evaluate_constant_logic
    from gx3cli.gx3_topology_conditions import _simplify
    for ct in ['p','f']:
        r=row(['a','SM','c','M'],[element(400,0,0,ct=ct),element(20,1,0,'cl')])
        tree=condition(r,'M20')
        assert tree['op']=='contact' and tree['ct_code']==ct,tree
        facts={'SM400':ConstantFact('SM400',True,'synthetic',(),())}
        assert evaluate_constant_logic(tree,facts).value is None
        assert _simplify(tree,facts)[1] is None
        assert evaluate_logic(tree,{'SM400':True})[0]=='unknown'
        ctx=StBuildContext();logic_to_st(tree,ctx)
        assert ctx.predicate_placeholders
        assert ('P ' if ct=='p' else 'F ') in logic_to_text(tree)


if __name__=='__main__':
    test_all_contacts_on_an_input_bus_reach_all_outputs()
    test_inv_negates_the_entire_input_not_an_extra_contact()
    test_edges_keep_their_input_and_are_unknown_in_one_snapshot()
    test_false_input_is_not_skipped_before_inv_or_falling_edge()
    test_parallel_arm_operator_excludes_common_input_before_split()
    test_fanout_to_another_output_does_not_create_a_parallel_arm_scope()
    test_trace_and_dependency_consumers_keep_expression_inputs_and_state_gap()
    test_predicate_tree_keeps_width_literals_and_operand_order()
    test_pulse_contacts_are_not_folded_to_level_constants()
    print('input bus and expression operator checks passed')
