from datetime import datetime, timedelta
import time
from openerp.osv import fields, osv
from openerp.tools.translate import _
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT , float_compare
import openerp.addons.decimal_precision as dp
from openerp import workflow

class sale_order(osv.osv):
    
    _inherit = "sale.order"
    

    def _get_picking_out(self, cr, uid, context=None):
        obj_data = self.pool.get('ir.model.data')
        return obj_data.get_object_reference(cr, uid, 'stock', 'picking_type_out')[1]
    
    _columns = {
                
                'state'             : fields.selection([
                                        ('draft', 'Draft'),
                                        ('sent', 'Quotation Sent'),
                                        ('cancel', 'Cancelled'),
                                        ('waiting_date', 'Waiting Schedule'),
                                        ('progress', 'Sales Order'),
                                        
                                        ('approved', 'Disetujui'),
                                        ('goods_sent', 'Barang Dikirim'),
                                        
                                        ('manual', 'Sale to Invoice'),
                                        ('shipping_except', 'Shipping Exception'),
                                        ('invoice_except', 'Invoice Exception'),
                                        
                                        ('done', 'Done'),
                                        ], 'Status', readonly=True, copy=False, help="Gives the status of the quotation or sales order.\
                                          \nThe exception status is automatically set when a cancel operation occurs \
                                          in the invoice validation (Invoice Exception) or in the picking list process (Shipping Exception).\nThe 'Waiting Schedule' status is set when the invoice is confirmed\
                                           but waiting for the scheduler to run on the order date.", select=True),
                'picking_type_id'   : fields.many2one('stock.picking.type', 'Deliver To', help="This will determine picking type of Delivery Order", #required=True,
                                           states={ 'approved': [('readonly', True)], 'done': [('readonly', True)]}),
                'nowo'              : fields.char('No Workorder'),
                'admin'             : fields.many2one('res.users', 'Sales'),
                'nopol'             : fields.many2one('no.polisi', 'Nomor Polisi'),
                'kilometer'         : fields.float('Kilometer'),
                'merek'             : fields.char('Nama Kendaraan'),
                'cara_pembayaran'   : fields.selection([('cash', 'Cash'), ('hutang', 'Hutang'), ('debitMandiri', 'Debit Mandiri'), ('debitBCA', 'Debit BCA'), ('debitBNI', 'Debit BNI'), ('ccardMandiri', 'Credit Card Mandri'), ('ccardBCA', 'Credit Card BCA'), ('ccardBNI', 'Credit Card BNI')], 'Cara Pembayaran'),
                
                }
    
    
    
    _defaults = {
                 'picking_type_id'  : _get_picking_out,
                 'nowo'             : '/',
                 'admin': lambda obj, cr, uid, context: uid,
                 }
    
    
    
    #==================================stock move=========================================
    
    def action_set_to_draft(self, cr, uid, ids, context=None):
        if context==None:
            context=context
        self.write(cr, uid, ids, {'state' : 'draft'}, context)
        return True


    def create(self, cr, uid, vals, context=None):
        if context is None:
            context = {}
        if vals.get('name', '/') == '/':
            vals['name'] = self.pool.get('ir.sequence').get(cr, uid, 'sale.order') or '/'
        
        if vals.get('nowo', '/') == '/':
            vals['nowo'] = self.pool.get('ir.sequence').get(cr, uid, 'work.order') or '/'    
        
        if vals.get('partner_id') and any(f not in vals for f in ['partner_invoice_id', 'partner_shipping_id', 'pricelist_id', 'fiscal_position']):
            defaults = self.onchange_partner_id(cr, uid, [], vals['partner_id'], context=context)['value']
            if not vals.get('fiscal_position') and vals.get('partner_shipping_id'):
                delivery_onchange = self.onchange_delivery_id(cr, uid, [], vals.get('company_id'), None, vals['partner_id'], vals.get('partner_shipping_id'), context=context)
                defaults.update(delivery_onchange['value'])
            vals = dict(defaults, **vals)
        ctx = dict(context or {}, mail_create_nolog=True)
        new_id = super(sale_order, self).create(cr, uid, vals, context=ctx)
        self.message_post(cr, uid, [new_id], body=_("Quotation created"), context=ctx)
        return new_id



    def _siapkan_invoice(self, cr, uid, order, lines, context=None):
        
        if context is None:
            context = {}
        journal_ids = self.pool.get('account.journal').search(cr, uid,
            [('type', '=', 'sale'), ('company_id', '=', order.company_id.id)],
            limit=1)
        if not journal_ids:
            raise osv.except_osv(_('Error!'),
                _('Please define sales journal for this company: "%s" (id:%d).') % (order.company_id.name, order.company_id.id))
        invoice_vals = {
            'name': order.client_order_ref or '',
            'origin': order.name,
            'type': 'out_invoice',
            'reference': order.client_order_ref or order.name,
            'account_id': order.partner_id.property_account_receivable.id,
            'partner_id': order.partner_invoice_id.id,
            'journal_id': journal_ids[0],
            'invoice_line': [(6, 0, lines)],
            'currency_id': order.pricelist_id.currency_id.id,
            'comment': order.note,
            'payment_term': order.payment_term and order.payment_term.id or False,
            'fiscal_position': order.fiscal_position.id or order.partner_id.property_account_position.id,
            'date_invoice': context.get('date_invoice', False),
            'company_id': order.company_id.id,
            'user_id': order.user_id and order.user_id.id or False,
            'section_id' : order.section_id.id
        }

        # Care for deprecated _inv_get() hook - FIXME: to be removed after 6.1
        invoice_vals.update(self._inv_get(cr, uid, order, context=context))
        return invoice_vals

    def _buatkan_invoice(self, cr, uid, order, lines, context=None):
        inv_obj = self.pool.get('account.invoice')
        obj_invoice_line = self.pool.get('account.invoice.line')
        if context is None:
            context = {}
        invoiced_sale_line_ids = self.pool.get('sale.order.line').search(cr, uid, [('order_id', '=', order.id), ('invoiced', '=', True)], context=context)
        from_line_invoice_ids = []
        for invoiced_sale_line_id in self.pool.get('sale.order.line').browse(cr, uid, invoiced_sale_line_ids, context=context):
            for invoice_line_id in invoiced_sale_line_id.invoice_lines:
                if invoice_line_id.invoice_id.id not in from_line_invoice_ids:
                    from_line_invoice_ids.append(invoice_line_id.invoice_id.id)
        for preinv in order.invoice_ids:
            if preinv.state not in ('cancel',) and preinv.id not in from_line_invoice_ids:
                for preline in preinv.invoice_line:
                    inv_line_id = obj_invoice_line.copy(cr, uid, preline.id, {'invoice_id': False, 'price_unit': -preline.price_unit})
                    lines.append(inv_line_id)
        inv = self._siapkan_invoice(cr, uid, order, lines, context=context)
        inv_id = inv_obj.create(cr, uid, inv, context=context)
        data = inv_obj.onchange_payment_term_date_invoice(cr, uid, [inv_id], inv['payment_term'], time.strftime(DEFAULT_SERVER_DATE_FORMAT))
        if data.get('value', False):
            inv_obj.write(cr, uid, [inv_id], data['value'], context=context)
        inv_obj.button_compute(cr, uid, [inv_id])
        return inv_id





    def onchange_nopol(self, cr, uid, ids, nopol, context=None):
        
        if nopol:
            nopolis = self.pool.get('no.polisi').browse(cr, uid, nopol, context=context)
            if nopolis and nopolis.partner_id:
                return {'value': {
                                 'partner_id' : nopolis.partner_id and nopolis.partner_id.id or False,
                                 'kilometer'  : nopolis.kilometer or 0,
                                 'merek'      : nopolis.merek or '',
                                 }}
        return {}




    def action_buat_invoice(self, cr, uid, ids, context=None):
#        if states is None:
#        states = ['goods_sent', 'done']
        print "AAAAAAAAAAAAAAAAAAAA"
        res = False
        invoices = {}
        invoice_ids = []
        invoice = self.pool.get('account.invoice')
        obj_sale_order_line = self.pool.get('sale.order.line')
        partner_currency = {}
        # If date was specified, use it as date invoiced, usefull when invoices are generated this month and put the
        # last day of the last month as invoice date
#        if date_invoice:
        if context==None:
            context=context
        for o in self.browse(cr, uid, ids, context=context):
            currency_id = o.pricelist_id.currency_id.id
            if (o.partner_id.id in partner_currency) and (partner_currency[o.partner_id.id] <> currency_id):
                raise osv.except_osv(
                    _('Error!'),
                    _('You cannot group sales having different currencies for the same partner.'))

            partner_currency[o.partner_id.id] = currency_id
            lines = []
            for line in o.order_line:
#                if line.invoiced:
#                    continue
#                if line.state == 'goods_sent':
                lines.append(line.id)
            created_lines = obj_sale_order_line.invoice_line_create(cr, uid, lines)
            print 'CREATE LINEESSS. ----------> ', created_lines
            if created_lines:
                invoices.setdefault(o.partner_invoice_id.id or o.partner_id.id, []).append((o, created_lines))
        if not invoices:
            for o in self.browse(cr, uid, ids, context=context):
                for i in o.invoice_ids:
                    if i.state == 'draft':
                        return i.id
                    
        print 'ISI INVOICE NYA ADALAAAAAAAAAAAAAAAAAHHHHHHH.---------->', invoices
        for val in invoices.values():
#            if grouped:
            res = self._buatkan_invoice(cr, uid, val[0][0], reduce(lambda x, y: x + y, [l for o, l in val], []), context=context)
            invoice_ref = ''
            origin_ref = ''
            for o, l in val:
                invoice_ref += (o.client_order_ref or o.name) + '|'
                origin_ref += (o.origin or o.name) + '|'
                self.write(cr, uid, [o.id], {'state': 'done'})
                cr.execute('insert into sale_order_invoice_rel (order_id,invoice_id) values (%s,%s)', (o.id, res))
                self.invalidate_cache(cr, uid, ['invoice_ids'], [o.id], context=context)
            #remove last '|' in invoice_ref
            if len(invoice_ref) >= 1:
                invoice_ref = invoice_ref[:-1]
            if len(origin_ref) >= 1:
                origin_ref = origin_ref[:-1]
            invoice.write(cr, uid, [res], {'origin': origin_ref, 'name': invoice_ref})
#            else:
#                for order, il in val:
#                    res = self._make_invoice(cr, uid, order, il, context=context)
#                    invoice_ids.append(res)
#                    self.write(cr, uid, [order.id], {'state': 'done'})
#                    cr.execute('insert into sale_order_invoice_rel (order_id,invoice_id) values (%s,%s)', (order.id, res))
#                    self.invalidate_cache(cr, uid, ['invoice_ids'], [order.id], context=context)
#        self.write(cr, uid, ids, {'state': 'done'}, context)
        return res









    def _prepare_order_line_move(self, cr, uid, order, order_line, picking_id, group_id, context=None):
        ''' prepare the stock move data from the PO line. This function returns a list of dictionary ready to be used in stock.move's create()'''
        product_uom = self.pool.get('product.uom')
        price_unit = order_line.price_unit
        if order_line.product_uom.id != order_line.product_id.uom_id.id:
            price_unit *= order_line.product_uom.factor / order_line.product_id.uom_id.factor
        if order.currency_id.id != order.company_id.currency_id.id:
            #we don't round the price_unit, as we may want to store the standard price with more digits than allowed by the currency
            price_unit = self.pool.get('res.currency').compute(cr, uid, order.currency_id.id, order.company_id.currency_id.id, price_unit, round=False, context=context)
        res = []
        move_template = {
            'name': order_line.name or '',
            'product_id': order_line.product_id.id,
            'product_uom': order_line.product_uom.id,
            'product_uos': order_line.product_uom.id,
            'product_uom_qty': order_line.product_uom_qty,
            'date': order.date_order,
            'date_expected': order.date_order,
            'location_id': order.picking_type_id.default_location_src_id.id, # 12, 
            'location_dest_id': order.partner_id.property_stock_customer.id, # order.location_id.id,
            'picking_id': picking_id,
            'partner_id': order.partner_id.id,
            'move_dest_id': False,
            'state': 'draft',
            'company_id': order.company_id.id,
            'price_unit': price_unit,
            'picking_type_id': order.picking_type_id.id,
            'group_id': group_id,
            'procurement_id': False,
            'origin': order.name,
            'route_ids': order.picking_type_id.warehouse_id and [(6, 0, [x.id for x in order.picking_type_id.warehouse_id.route_ids])] or [],
            'warehouse_id':order.picking_type_id.warehouse_id.id,
            'invoice_state': 'invoiced', # order.invoice_method == 'picking' and '2binvoiced' or 'none',
        }

        diff_quantity = order_line.product_uom_qty
#        for procurement in order_line.procurement_ids:
#            procurement_qty = product_uom._compute_qty(cr, uid, procurement.product_uom.id, procurement.product_qty, to_uom_id=order_line.product_uom.id)
#            tmp = move_template.copy()
#            tmp.update({
#                'product_uom_qty': min(procurement_qty, diff_quantity),
#                'product_uos_qty': min(procurement_qty, diff_quantity),
#                'move_dest_id': procurement.move_dest_id.id,  #move destination is same as procurement destination
#                'group_id': procurement.group_id.id or group_id,  #move group is same as group of procurements if it exists, otherwise take another group
#                'procurement_id': procurement.id,
#                'invoice_state': procurement.rule_id.invoice_state or (procurement.location_id and procurement.location_id.usage == 'customer' and procurement.invoice_state=='picking' and '2binvoiced') or (order.invoice_method == 'picking' and '2binvoiced') or 'none', #dropship case takes from sale
#                'propagate': procurement.rule_id.propagate,
#            })
#            diff_quantity -= min(procurement_qty, diff_quantity)
#            res.append(tmp)
#        if diff_quantity > 0:
#            move_template['product_uom_qty'] = diff_quantity
#            move_template['product_uos_qty'] = diff_quantity
        res.append(move_template)
        return res




    def _create_stock_moves(self, cr, uid, order, order_lines, picking_id=False, context=None):
        
        stock_move = self.pool.get('stock.move')
        stock_picking = self.pool.get('stock.picking')
        todo_moves = []
        new_group = self.pool.get("procurement.group").create(cr, uid, {'name': order.name, 'partner_id': order.partner_id.id}, context=context)

        for order_line in order_lines:
            if not order_line.product_id:
                continue

            if order_line.product_id.type in ('product', 'consu'):
                for vals in self._prepare_order_line_move(cr, uid, order, order_line, picking_id, new_group, context=context):
                    move = stock_move.create(cr, uid, vals, context=context)
                    todo_moves.append(move)

        todo_moves = stock_move.action_confirm(cr, uid, todo_moves)   
        stock_move.force_assign(cr, uid, todo_moves)
        stock_picking.do_transfer(cr, uid, picking_id, context=context)


    def test_moves_done(self, cr, uid, ids, context=None):
        '''PO is done at the delivery side if all the incoming shipments are done'''
        for purchase in self.browse(cr, uid, ids, context=context):
            for picking in purchase.picking_ids:
                if picking.state != 'done':
                    return False
        return True

    def test_moves_except(self, cr, uid, ids, context=None):
        ''' PO is in exception at the delivery side if one of the picking is canceled
            and the other pickings are completed (done or canceled)
        '''
        at_least_one_canceled = False
        alldoneorcancel = True
        for purchase in self.browse(cr, uid, ids, context=context):
            for picking in purchase.picking_ids:
                if picking.state == 'cancel':
                    at_least_one_canceled = True
                if picking.state not in ['done', 'cancel']:
                    alldoneorcancel = False
        return at_least_one_canceled and alldoneorcancel

    def move_lines_get(self, cr, uid, ids, *args):
        res = []
        for order in self.browse(cr, uid, ids, context={}):
            for line in order.order_line:
                res += [x.id for x in line.move_ids]
        return res

    def action_picking_create(self, cr, uid, ids, context=None):
        for order in self.browse(cr, uid, ids):
            picking_vals = {
                'picking_type_id': order.picking_type_id.id,
                'partner_id': order.partner_id.id,
                'date': order.date_order,
                'origin': order.name,
            }
            picking_id = self.pool.get('stock.picking').create(cr, uid, picking_vals, context=context)
            self._create_stock_moves(cr, uid, order, order.order_line, picking_id, context=context)
        self.write(cr, uid, ids, {'state' : 'goods_sent'}, context)
    
    
    
    
    def _cek_stock(self, cr, uid, product, qty=0, uom=False, qty_uos=0, uos=False,  context = None):
        context = context or {}
        res = True
        product_uom_obj = self.pool.get('product.uom')
        product_obj = self.pool.get('product.product')
        warehouse_obj = self.pool['stock.warehouse']
        warning = {}
        #UoM False due to hack which makes sure uom changes price, ... in product_id_change
#        res = self.product_id_change(cr, uid, ids, pricelist, product, qty=qty,
#            uom=False, qty_uos=qty_uos, uos=uos, name=name, partner_id=partner_id,
#            lang=lang, update_tax=update_tax, date_order=date_order, packaging=packaging, fiscal_position=fiscal_position, flag=flag, context=context)

        if not product:
#            res['value'].update({'product_packaging': False})
            return res

        #update of result obtained in super function
        product_obj = product_obj.browse(cr, uid, product, context=context)
#        res['value'].update({'product_tmpl_id': product_obj.product_tmpl_id.id, 'delay': (product_obj.sale_delay or 0.0)})

        # Calling product_packaging_change function after updating UoM
#        res_packing = self.product_packaging_change(cr, uid, ids, pricelist, product, qty, uom, partner_id, packaging, context=context)
#        res['value'].update(res_packing.get('value', {}))
#        warning_msgs = res_packing.get('warning') and res_packing['warning']['message'] or ''

        if product_obj.type == 'product':
            #determine if the product is MTO or not (for a further check)
            isMto = False
#            if warehouse_id:
#                warehouse = warehouse_obj.browse(cr, uid, warehouse_id, context=context)
#                for product_route in product_obj.route_ids:
#                    if warehouse.mto_pull_id and warehouse.mto_pull_id.route_id and warehouse.mto_pull_id.route_id.id == product_route.id:
#                        isMto = True
#                        break
#            else:
#                try:
#                    mto_route_id = warehouse_obj._get_mto_route(cr, uid, context=context)
#                except:
#                    # if route MTO not found in ir_model_data, we treat the product as in MTS
#                    mto_route_id = False
#                if mto_route_id:
#                    for product_route in product_obj.route_ids:
#                        if product_route.id == mto_route_id:
#                            isMto = True
#                            break

            #check if product is available, and if not: raise a warning, but do this only for products that aren't processed in MTO
            if not isMto:
                uom_record = False
                if uom:
                    uom_record = product_uom_obj.browse(cr, uid, uom, context=context)
                    if product_obj.uom_id.category_id.id != uom_record.category_id.id:
                        uom_record = False
                if not uom_record:
                    uom_record = product_obj.uom_id
                compare_qty = float_compare(product_obj.virtual_available, qty, precision_rounding=uom_record.rounding)
                if compare_qty == -1:
                    res = False
#                    warn_msg = _('You plan to sell %.2f %s but you only have %.2f %s available !\nThe real stock is %.2f %s. (without reservations)') % \
#                        (qty, uom_record.name,
#                         max(0,product_obj.virtual_available), uom_record.name,
#                         max(0,product_obj.qty_available), uom_record.name)
#                    warning_msgs += _("Not enough stock ! : ") + warn_msg + "\n\n"

        #update of warning messages
#        if warning_msgs:
#            warning = {
#                       'title': _('Configuration Error!'),
#                       'message' : warning_msgs
#                    }
#        res.update({'warning': warning})
        return res
    
    
    def action_approve(self, cr, uid,ids, context=None):
        nopol = self.pool.get('no.polisi')
        if context==None:
            context=context
        for so in self.browse(cr, uid, ids, context=context):
            if so.nopol:
                nopol.write(cr, uid, so.nopol.id, {'kilometer' : so.kilometer, 'partner_id' : so.partner_id and so.partner_id.id or False, 'merek' : so.merek or ''})
        
        for so2 in self.browse(cr, uid, ids, context=context):
            if so2.order_line:
                for line in so2.order_line:
                    res = self._cek_stock(cr, uid, line.product_id.id,  line.product_uos_qty, line.product_uom.id, line.product_uos.id, context=context)
                    if not res:
                        raise osv.except_osv(_('Peringatan!'),
                               _('Stock Barang ini tidak mencukupi : "%s" (id:%d).') % \
                                   (line.product_id.name, line.product_id.id,))
        self.write(cr, uid, ids, {'state' : 'approved'}, context)
        return True
    
    #===========================================================================

sale_order()

class sale_order_line(osv.osv):
    _inherit = "sale.order.line"
    _columns = {
                'mechanic'          : fields.many2one('res.users', 'Mechanic'),
                'admin_id'          : fields.related('order_id', 'admin', type='many2one', relation='res.users', string='Admin', store=True),
                }


class no_polisi(osv.osv):    
    _name = "no.polisi"
    _description = "No Polisi"
    _columns = {
                'name'      : fields.char('No. Polisi'),
                'kilometer' : fields.float('Kilometer'),
                'partner_id': fields.many2one('res.partner', 'Pemilik'),
                'merek'     : fields.char('Nama Kendaraan'),
                }



#class product_product(osv.osv):
#    _inherit = "product.product"
#    
#    _columns = {
#                'code_product'          : fields.char('Code Product'),
#                }
#    
    
    
    
    
    
    
    
    
    
    
    
    
    
    

    
    