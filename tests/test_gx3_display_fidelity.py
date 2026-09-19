"""Synthetic intermediate rows pin literal, modifier and opcode spelling.

These exercise parse_rung, not just hand-constructed display objects. They do
not establish execution semantics or cross-reference address resolution.
"""
from gx3cli.gx3_ladder_print import parse_rung, display_operands
from gx3cli.review_gx3_project import LadderRow


def row(opcode, tokens, types, args, kind='cl'):
    header = [opcode, *tokens]
    data = ('V1:' + str(len(header)) + ':' + ':'.join(str(len(t)) for t in header)
            + ':' + ':'.join(header) + ':cb{fg=fg{dim=8x1:es=['
            + 'e{s=ce{op=' + kind + '{op=#:ct=a:as=['
            + ':'.join('as{vt=' + t + '}' for t in types) + ']}:args=['
            + ':'.join(args) + ']}:pos=0,0}]}}')
    return LadderRow(lddb='test_LDDB.db', pos=1, block_id='1', title='',
                     blocktype=0, rowsize=1, data=data, dim='', operations=[], parse_status='')


def dev(n):
    return 'd{s=#:a=' + str(n) + ':vt=nn}'


def test_multiply_width_comes_from_source_not_wider_destination():
    for opcode, types, expected in [
        ('*', ['A16s', 'A16s', 'A32s'], '*'),
        ('*P', ['A16s', 'A16s', 'A32s'], '*P'),
        ('*', ['A32s', 'A32s', 'A64s'], 'D*'),
        ('D*', ['A32s', 'A32s', 'A64s'], 'D*'),
        ('=', ['A32s', 'A32s'], 'D='),
    ]:
        args=[dev(10), dev(20), dev(30)][:len(types)]
        ops, _, _ = parse_rung(row(opcode, ['D']*len(args), types, args))
        assert len(ops)==1 and ops[0].opcode_text()==expected, (opcode, ops)


def test_strings_are_header_literals_including_empty_and_token_like_values():
    for literal in ['', 'HELLO', 'D', 'MOV', '#', '007']:
        r=row('$MOV', ['String', literal, '"'+literal+'"', 'D'],
              ['Ass', 'Ass'], ['c{s=#:v=#:t=#}', dev(10)])
        ops, _, _=parse_rung(r)
        assert len(ops)==1, literal
        assert ops[0].operands==['"'+literal+'"','D10'], (literal, ops[0].operands)
    for token, expected in [('=', '$='), ('<>', '$<>')]:
        ops, _, _=parse_rung(row(token,['D','D'],['Ass','Ass'],[dev(10),dev(20)],'ct'))
        assert ops[0].opcode_text()==expected


def test_index_width_and_indirection_survive_with_following_operands():
    indexed='M{b='+dev(40)+':m='+dev(2)+'}'
    indirect='M{b='+indexed+':m=c{s=#:v=0}}'
    for raw,tokens,expected in [
        (indexed,['ZR','ZZs'],'ZR40ZZ2'),
        (indexed,['D','Zs'],'D40Z2'),
        (indirect,['D','Zs','Ats'],'@D40Z2'),
        ('M{b='+dev(40)+':m=c{s=#:v=0}}',['D','Ats'],'@D40'),
        ('M{b='+dev(2)+':m='+dev(2)+'}',['D','Zs'],'D2Z2'),
    ]:
        ops, _, _=parse_rung(row('MOV',tokens+['D'],['A16','A16'],[raw,dev(50)]))
        assert ops[0].operands==[expected,'D50'], (tokens,ops[0].operands)


if __name__=='__main__':
    test_multiply_width_comes_from_source_not_wider_destination()
    test_strings_are_header_literals_including_empty_and_token_like_values()
    test_index_width_and_indirection_survive_with_following_operands()
    print('display fidelity checks passed')


def test_nested_modifier_order_and_typed_constants():
    bit = 'M{b='+dev(12)+':m=c{s=#:v=0}}'
    indexed = 'M{b='+dev(12)+':m='+dev(1)+'}'
    for raw,tokens,expected in [
        ('M{b='+bit+':m='+dev(1)+'}',['ZR','Dots','Zs'],'ZR12.0Z1'),
        ('M{b='+indexed+':m=c{s=#:v=0}}',['ZR','Zs','Dots'],'ZR12Z1.0'),
    ]:
        ops,_,_=parse_rung(row('MOV',tokens+['D'],['A16','A16'],[raw,dev(30)]))
        assert ops[0].operands==[expected,'D30'],ops[0].operands
        from gx3cli.gx3_arg_decode import decode_args
        occurrences=decode_args([raw,dev(30)],tokens+['D'],'MOV')
        assert occurrences[0].device=='ZR12'
        assert 'bit=K0' in occurrences[0].detail and 'Z1 indexed' in occurrences[0].detail
        assert occurrences[1].device=='Z1' and occurrences[-1].device=='D30'
    for kind,expected in [('A16s','HFFFF'),('A32s','HFFFFFFFF')]:
        ops,_,_=parse_rung(row('MOV',['H_1','D'],[kind,kind],['c{s=#:v=-1}',dev(30)]))
        assert ops[0].operands[0]==expected
    ops,_,_=parse_rung(row('FROM',['U','D'],['A16','A16'],[dev(42),dev(30)]))
    assert ops[0].operands==['U2A','D30']


def test_delimiter_literals_and_multiline_titles():
    from gx3cli.review_gx3_project import extract_title
    for literal in [':', 'a:b', ':cb{']:
        ops,_,_=parse_rung(row('$MOV',['String',literal,'"'+literal+'"','D'],['Ass','Ass'],['c{s=#:v=#:t=#}',dev(30)]))
        assert ops[0].operands==['"'+literal+'"','D30'],ops[0].operands
    from gx3cli.extract_gx3_extended_instruction_knowledge import header_tokens
    literal='A😀:B'
    units=len(literal.encode('utf-16-le'))//2
    assert header_tokens(f'V1:1:{units}:{literal}:st{{}}')[-1]==literal
    text=' Heading:\nsecond line '
    assert extract_title(f'V1:1:{len(text)}:{text}:st{{}}')==text


if __name__=='__main__':
    test_nested_modifier_order_and_typed_constants()
    test_delimiter_literals_and_multiline_titles()


def test_buffer_modifier_order_matches_occurrence_name():
    from gx3cli.gx3_arg_decode import decode_args
    base='B{b='+dev(42)+':e='+dev(10)+':vt=i}'
    indexed='M{b='+base+':m='+dev(0)+'}'
    raw='M{b='+indexed+':m=c{s=#:v=13}}'
    tokens=['Us','G','Zs','Dots','D']
    ops,_,_=parse_rung(row('MOV',tokens,['A16','A16'],[raw,dev(30)]))
    occurrences=decode_args([raw,dev(30)],tokens,'MOV')
    assert ops[0].operands==['U2A\\G10Z0.D','D30']
    assert occurrences[0].device==ops[0].operands[0]
    assert 'indexed' in occurrences[0].detail and 'bit=13' in occurrences[0].detail
    assert occurrences[1].device=='Z0'


if __name__=='__main__':
    test_buffer_modifier_order_matches_occurrence_name()
